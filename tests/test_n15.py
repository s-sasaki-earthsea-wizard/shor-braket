# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import numpy as np
import pytest
from braket.circuits import Circuit
from typer.testing import CliRunner

from shor_braket.analysis.distribution import expected_joint_probabilities, total_variation_distance
from shor_braket.cli import app
from shor_braket.devices import summarize_snapshot
from shor_braket.quantum.compile import compile_to_native
from shor_braket.quantum.n15 import (
    ORACLE_GENERIC_CONSTANT,
    ORACLE_GENERIC_REPEATED,
    add_fredkin,
    add_inverse_qft,
    add_toffoli,
    build_n15_circuit,
)
from shor_braket.quantum.native import PRX_CZ, PRX_XX, gate_family
from shor_braket.quantum.reference import _inverse_fourier_matrix
from shor_braket.quantum.routing import choose_layout, route_circuit
from shor_braket.runner.local import run_local
from shor_braket.runner.n15 import (
    apply_readout_flips,
    emulate_n15_configuration,
    reorder_probabilities,
    run_n15_emulation,
)


def _same_up_to_phase(a: np.ndarray, b: np.ndarray) -> bool:
    return abs(abs(np.trace(a.conj().T @ b)) - a.shape[0]) < 1e-9


def _bit_reversal(n: int) -> np.ndarray:
    matrix = np.zeros((1 << n, 1 << n))
    for j in range(1 << n):
        matrix[int(format(j, f"0{n}b")[::-1], 2), j] = 1
    return matrix


def _joint(circuit: Circuit, order: list[int]) -> np.ndarray:
    measured = sorted(order)
    raw = np.asarray(run_local(circuit.copy().probability(target=measured), shots=0).values[0])
    return reorder_probabilities(raw, measured, order)


def test_building_blocks_match_the_sdk_gates():
    assert _same_up_to_phase(
        add_toffoli(Circuit(), 0, 1, 2).to_unitary(), Circuit().ccnot(0, 1, 2).to_unitary()
    )
    assert _same_up_to_phase(
        add_fredkin(Circuit(), 0, 1, 2).to_unitary(), Circuit().cswap(0, 1, 2).to_unitary()
    )
    for n in (2, 3, 4):
        unitary = add_inverse_qft(Circuit(), tuple(range(n))).to_unitary()
        assert _same_up_to_phase(_bit_reversal(n) @ unitary, _inverse_fourier_matrix(n))


@pytest.mark.parametrize("t", [2, 4])
@pytest.mark.parametrize("mode", [ORACLE_GENERIC_CONSTANT, ORACLE_GENERIC_REPEATED])
def test_n15_circuit_reproduces_the_ideal_joint_distribution(t, mode):
    if mode == ORACLE_GENERIC_REPEATED and t == 4:
        pytest.skip("432 two-qubit gates; the t = 2 case covers the repeated oracle")
    logical = build_n15_circuit(count_qubit_count=t, oracle_mode=mode)
    expected = expected_joint_probabilities(
        modulus=15, base=7, count_qubit_count=t, work_qubit_count=4
    )
    joint = _joint(logical.circuit, [*logical.count_qubits, *logical.work_qubits])

    assert total_variation_distance(joint, expected) < 1e-10
    assert logical.count_qubits == tuple(reversed(logical.count_qubits_physical))
    assert logical.multipliers[-2:] == (4, 7)
    # t = 4 adds two identity multipliers (no gates) and ten inverse-QFT CNOTs
    assert (
        logical.two_qubit_gates
        == (46 if mode == ORACLE_GENERIC_CONSTANT else 86) + {2: 0, 4: 10}[t]
    )


def test_n15_circuit_rejects_bad_inputs():
    with pytest.raises(ValueError, match="unit modulo 15"):
        build_n15_circuit(base=5)
    with pytest.raises(ValueError, match="oracle_mode"):
        build_n15_circuit(oracle_mode="compiled")


@pytest.mark.parametrize("family", [PRX_CZ, PRX_XX])
def test_native_lowering_preserves_the_unitary_and_distribution(family):
    logical = Circuit().h(0).t(0).cnot(0, 1).ti(1).cnot(1, 0).s(0).swap(0, 1).rz(1, 0.3)
    compiled = compile_to_native(logical, family)
    restored = compiled.circuit.copy()
    for qubit, phase in compiled.dropped_final_z.items():
        restored.rz(qubit, phase)
    assert _same_up_to_phase(restored.to_unitary(), logical.to_unitary())
    names = {instruction.operator.name for instruction in compiled.circuit.instructions}
    assert names <= ({"PRx", "CZ"} if family == PRX_CZ else {"PRx", "XX", "Rz"})

    n15 = build_n15_circuit(count_qubit_count=2)
    order = [*n15.count_qubits, *n15.work_qubits]
    native = compile_to_native(n15.circuit, family)
    assert native.two_qubit_gates == 46
    np.testing.assert_allclose(_joint(native.circuit, order), _joint(n15.circuit, order), atol=1e-9)


def test_reorder_and_readout_helpers():
    probabilities = np.zeros(8)
    probabilities[0b100] = 1.0  # qubit 0 set, in ascending order [0, 1, 2]
    reordered = reorder_probabilities(probabilities, [0, 1, 2], [2, 1, 0])
    assert int(np.argmax(reordered)) == 0b001
    flipped = apply_readout_flips(np.array([1.0, 0.0]), [0.1])
    np.testing.assert_allclose(flipped, [0.9, 0.1])
    with pytest.raises(ValueError):
        reorder_probabilities(probabilities, [0, 1, 2], [0, 1, 1])


def test_routing_on_a_lattice_preserves_the_distribution(garnet_snapshot):
    summary = summarize_snapshot(garnet_snapshot)
    logical = build_n15_circuit(count_qubit_count=2)
    qubits = sorted([*logical.count_qubits_physical, *logical.work_qubits])
    routed = choose_layout(logical.circuit, summary, qubits, max_permutations=6)

    assert routed.swap_count > 0
    assert routed.two_qubit_gates == 46 + 3 * routed.swap_count
    for name, targets in (
        (instruction.operator.name, instruction.target)
        for instruction in routed.circuit.instructions
    ):
        if len(targets) == 2:
            assert summary.is_adjacent(int(targets[0]), int(targets[1])), name
    order = [*logical.count_qubits, *logical.work_qubits]
    physical = [routed.final_layout[q] for q in order]
    np.testing.assert_allclose(
        _joint(routed.circuit, physical), _joint(logical.circuit, order), atol=1e-9
    )


def test_all_to_all_device_needs_no_swaps(ibex_snapshot):
    summary = summarize_snapshot(ibex_snapshot)
    logical = build_n15_circuit(count_qubit_count=2)
    qubits = sorted([*logical.count_qubits_physical, *logical.work_qubits])
    routed = route_circuit(
        logical.circuit, summary, dict(zip(qubits, [0, 1, 2, 3, 4, 5], strict=True))
    )

    assert routed.swap_count == 0
    assert routed.two_qubit_gates == 46


def test_configuration_is_accepted_and_scored(garnet_snapshot):
    summary = summarize_snapshot(garnet_snapshot)
    config = emulate_n15_configuration(
        garnet_snapshot, summary, oracle_mode=ORACLE_GENERIC_CONSTANT, shots=200, max_permutations=4
    )

    assert config["validation"]["accepted"] is True
    assert config["gate_family"] == gate_family(summary.native_gate_set)
    assert config["ideal_check_tvd"] < 1e-9
    metrics = config["metrics"]
    assert 0.0 < metrics["exact_tvd"] < metrics["tvd_ideal_vs_uniform"]
    assert 0.0 < metrics["signal_fraction_exact"] < 1.0
    assert metrics["order_recovery_baseline_uniform_y"] == 0.75
    assert 0.6 < metrics["order_recovery_rate"] < 0.8
    assert 0.6 < metrics["order_recovery_rate_sampled"] < 0.8
    assert set(config["layout"]["roles"].values()) == {"c0", "c1", "w0", "w1", "w2", "w3"}


def test_report_is_written_without_a_validated_record(snapshot_dir, tmp_path):
    report = run_n15_emulation(
        device_keys=["ibex"],
        oracle_modes=[ORACLE_GENERIC_CONSTANT],
        shots=100,
        output_dir=tmp_path,
        snapshot_dir=snapshot_dir,
        visualize=False,
        max_permutations=2,
    )

    assert report["execution"]["class"] == "local-emulator-n15"
    assert report["qpu_gate"]["qpu_eligible"] is False
    artifact = Path(report["artifact_path"])
    stored = json.loads(artifact.read_text())
    config = stored["configurations"]["ibex/generic-constant"]
    assert (artifact.parent / config["openqasm_path"]).stat().st_size > 0
    assert "openqasm" not in config
    assert not (tmp_path / "validated").exists()


def test_emulate_n15_cli_rejects_unknown_oracle(snapshot_dir):
    result = CliRunner().invoke(
        app, ["emulate-n15", "--oracle", "compiled", "--snapshot-dir", str(snapshot_dir)]
    )

    assert result.exit_code == 1
    assert "not an oracle mode" in result.output
