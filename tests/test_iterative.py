# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

import json
from math import pi
from pathlib import Path

import numpy as np
import pytest
from braket.circuits import Circuit
from braket.devices import LocalSimulator
from typer.testing import CliRunner

from shor_braket.analysis.distribution import (
    expected_joint_probabilities,
    hellinger_fidelity,
    sampling_floor,
    sampling_sweep,
    support_signal_fraction,
    total_variation_distance,
)
from shor_braket.cli import app
from shor_braket.devices import summarize_snapshot
from shor_braket.quantum.compile import compile_to_native
from shor_braket.quantum.feedforward import (
    deferred_measurement_circuit,
    experimental_capabilities,
    feed_forward_violations,
    supports_feed_forward,
)
from shor_braket.quantum.n15 import ORACLE_GENERIC_CONSTANT, ORACLE_GENERIC_REPEATED
from shor_braket.quantum.n15_iterative import build_n15_iterative_circuit
from shor_braket.quantum.native import PRX_CZ, PRX_XX
from shor_braket.quantum.routing import choose_layout
from shor_braket.runner.n15 import reorder_probabilities
from shor_braket.runner.n15_iterative import (
    emulate_iterative_configuration,
    run_iterative_emulation,
)


def _expected(t: int) -> np.ndarray:
    return expected_joint_probabilities(modulus=15, base=7, count_qubit_count=t, work_qubit_count=4)


def _deferred_joint(circuit: Circuit, order: list[int], first_ancilla: int = 100) -> np.ndarray:
    measured = sorted(order)
    deferred = deferred_measurement_circuit(circuit, first_ancilla)
    result = (
        LocalSimulator("braket_sv")
        .run(deferred.circuit.probability(target=measured), shots=0)
        .result()
    )
    return reorder_probabilities(np.asarray(result.values[0]), measured, order)


def test_two_pi_pulses_make_a_z_rotation():
    alpha = 0.9
    pair = Circuit().prx(0, pi, 0.0).prx(0, pi, alpha / 2).to_unitary()
    rz = Circuit().rz(0, alpha).to_unitary()
    assert abs(abs(np.trace(pair.conj().T @ rz)) - 2) < 1e-9


@pytest.mark.parametrize(
    ("t", "mode", "two_qubit"),
    [
        (2, ORACLE_GENERIC_CONSTANT, 44),
        (2, ORACLE_GENERIC_REPEATED, 84),
        (3, ORACLE_GENERIC_CONSTANT, 44),
    ],
)
def test_iterative_circuit_reproduces_the_ideal_joint_distribution(t, mode, two_qubit):
    logical = build_n15_iterative_circuit(count_qubit_count=t, oracle_mode=mode)
    joint = _deferred_joint(logical.circuit, [*logical.count_qubits, *logical.work_qubits])

    assert total_variation_distance(joint, _expected(t)) < 1e-10
    assert logical.two_qubit_gates == two_qubit
    assert logical.feed_forward == {"measure_ff": t - 1, "cc_prx": 2 * (t - 1) + t * (t - 1)}
    assert logical.count_qubits[0] == logical.count_qubit
    assert len(logical.record_qubits) == t - 1
    assert feed_forward_violations(logical.circuit) == []
    assert logical.multipliers[-2:] == (4, 7)


def test_feed_forward_sampled_run_stays_on_the_ideal_support():
    logical = build_n15_iterative_circuit(count_qubit_count=2)
    result = LocalSimulator("braket_sv").run(logical.circuit, shots=300).result()
    order = [*logical.count_qubits, *logical.work_qubits]
    measured = [int(q) for q in result.measured_qubits]
    support = {
        (entry["count_value"], entry["work_value"])
        for entry in (
            {"count_value": index // 16, "work_value": index % 16}
            for index in np.flatnonzero(_expected(2) > 1e-9)
        )
    }
    for state in result.measurement_counts:
        bits = {qubit: state[i] for i, qubit in enumerate(measured)}
        y = int("".join(bits[q] for q in order[:2]), 2)
        work = int("".join(bits[q] for q in order[2:]), 2)
        assert (y, work) in support


def test_deferred_transform_and_checker_reject_bad_keys():
    with experimental_capabilities():
        early = Circuit().cc_prx(0, pi, 0.0, 0).measure_ff(0, 0)
        twice = Circuit().measure_ff(0, 0).measure_ff(1, 0)
        two_controllers = (
            Circuit().h(0).measure_ff(0, 0).h(1).measure_ff(1, 1).cc_prx(2, pi, 0.0, 0)
        )
        two_controllers.cc_prx(2, pi, 0.0, 1)
    with pytest.raises(ValueError, match="before it is measured"):
        deferred_measurement_circuit(early, 10)
    with pytest.raises(ValueError, match="measured twice"):
        deferred_measurement_circuit(twice, 10)
    assert any("before measure_ff" in v for v in feed_forward_violations(early))
    assert any("more than one" in v for v in feed_forward_violations(twice))
    assert any("only one controller" in v for v in feed_forward_violations(two_controllers))
    assert supports_feed_forward(["prx", "cz", "cc_prx", "measure_ff"])
    assert not supports_feed_forward(["prx", "xx", "rz"])


def test_native_lowering_keeps_feed_forward_and_the_distribution():
    logical = build_n15_iterative_circuit(count_qubit_count=2)
    order = [*logical.count_qubits, *logical.work_qubits]
    native = compile_to_native(logical.circuit, PRX_CZ)

    assert native.gate_counts["cc_prx"] == 4
    assert native.gate_counts["measure_ff"] == 1
    assert native.two_qubit_gates == 44
    names = {instruction.operator.name for instruction in native.circuit.instructions}
    assert names <= {"PRx", "CZ", "CCPRx", "MeasureFF"}
    assert total_variation_distance(_deferred_joint(native.circuit, order), _expected(2)) < 1e-9
    with pytest.raises(NotImplementedError, match="feed-forward"):
        compile_to_native(logical.circuit, PRX_XX)


def test_router_places_the_record_qubit_off_the_core(garnet_snapshot):
    summary = summarize_snapshot(garnet_snapshot)
    logical = build_n15_iterative_circuit(count_qubit_count=2)
    routed = choose_layout(
        logical.circuit,
        summary,
        sorted(logical.core_qubits),
        max_permutations=6,
        detached=logical.record_qubits,
    )
    record = routed.final_layout[logical.record_qubits[0]]
    core = {routed.final_layout[q] for q in logical.core_qubits}

    assert record not in core
    assert routed.swap_count > 0
    assert routed.two_qubit_gates == 44 + 3 * routed.swap_count
    assert routed.error_budget > 0
    order = [*logical.count_qubits, *logical.work_qubits]
    physical = [routed.final_layout[q] for q in order]
    np.testing.assert_allclose(
        _deferred_joint(routed.circuit, physical, first_ancilla=200),
        _deferred_joint(logical.circuit, order),
        atol=1e-9,
    )
    with pytest.raises(ValueError, match="detached"):
        choose_layout(
            logical.circuit, summary, sorted(logical.core_qubits), detached=(logical.count_qubit,)
        )


def test_iterative_configuration_is_accepted_and_scored(garnet_snapshot):
    summary = summarize_snapshot(garnet_snapshot)
    config = emulate_iterative_configuration(
        garnet_snapshot,
        summary,
        oracle_mode=ORACLE_GENERIC_CONSTANT,
        shots=100,
        max_permutations=4,
        emulator_check_shots=0,
    )

    assert config["validation"]["accepted"] is True
    assert config["feed_forward"]["supported"] is True
    assert config["feed_forward"]["violations"] == []
    assert config["ideal_check_tvd"] < 1e-9
    m = config["metrics"]
    assert 0.0 < m["exact_tvd"] < m["tvd_ideal_vs_uniform"]
    assert 0.0 < m["signal_fraction_exact"] < 1.0
    assert abs(m["signal_fraction_exact"] - m["support_signal_fraction_exact"]) < 0.1
    assert 0.0 < m["predicted_signal_fraction"] < 1.0
    assert m["gate_noise_only_tvd"] < m["exact_tvd"]
    assert m["order_recovery_baseline_uniform_y"] == 0.75
    roles = set(config["layout"]["roles"].values())
    assert roles == {"c0", "c1", "w0", "w1", "w2", "w3"}


def test_device_without_feed_forward_is_rejected(ibex_snapshot):
    summary = summarize_snapshot(ibex_snapshot)
    config = emulate_iterative_configuration(
        ibex_snapshot, summary, oracle_mode=ORACLE_GENERIC_CONSTANT, shots=10
    )

    assert config["feed_forward"]["supported"] is False
    assert config["validation"]["accepted"] is False
    assert config["validation"]["error_type"] == "FeedForwardUnsupported"


def test_sampling_helpers_match_a_monte_carlo():
    expected = _expected(2)
    rng = np.random.default_rng(1)
    samples = rng.multinomial(1000, expected, size=400) / 1000
    observed = float((0.5 * np.abs(samples - expected).sum(axis=1)).mean())
    assert abs(sampling_floor(expected, 1000) - observed) / observed < 0.1

    uniform = np.full_like(expected, 1 / expected.size)
    mixture = 0.95 * expected + 0.05 * uniform
    support = expected > 1e-9
    assert abs(support_signal_fraction(float(mixture[support].sum()), 0.25) - 0.95) < 1e-12
    assert abs(hellinger_fidelity(expected, expected) - 1.0) < 1e-12

    # Near the ideal distribution the per-bin gap is below the sampling noise, so the TVD-based
    # estimate is biased low while the support-mass estimate stays centred on the truth.
    sweep = sampling_sweep(mixture, expected, shots_list=[200, 2000], repetitions=100)
    assert [row["shots"] for row in sweep["rows"]] == [200, 2000]
    assert abs(sweep["exact_signal_fraction_support"] - 0.95) < 1e-9
    for row in sweep["rows"]:
        assert row["signal_fraction_tvd"]["mean"] < 0.95 - 0.01
        assert abs(row["signal_fraction_support"]["mean"] - 0.95) < 0.03
    assert (
        sweep["rows"][0]["signal_fraction_tvd"]["mean"]
        < sweep["rows"][1]["signal_fraction_tvd"]["mean"]
    )


def test_report_is_written_for_the_iterative_method(snapshot_dir, tmp_path):
    report = run_iterative_emulation(
        device_keys=["garnet"],
        oracle_modes=[ORACLE_GENERIC_CONSTANT],
        shots=50,
        sweep_shots=(100,),
        repetitions=5,
        include_standard=False,
        output_dir=tmp_path,
        snapshot_dir=snapshot_dir,
        visualize=False,
        max_permutations=2,
        emulator_check_shots=0,
    )

    assert report["execution"]["class"] == "local-emulator-n15-iterative"
    assert report["qpu_gate"]["qpu_eligible"] is False
    key = "garnet/generic-constant/iterative-qpe"
    assert key in report["sampling"]
    artifact = Path(report["artifact_path"])
    stored = json.loads(artifact.read_text())
    config = stored["configurations"][key]
    assert (artifact.parent / config["openqasm_path"]).stat().st_size > 0
    assert "measure_ff" in (artifact.parent / config["openqasm_path"]).read_text()
    assert "openqasm" not in config


def test_emulate_n15_iterative_cli_rejects_bad_sweep(snapshot_dir):
    result = CliRunner().invoke(
        app,
        ["emulate-n15-iterative", "--sweep-shots", "a,b", "--snapshot-dir", str(snapshot_dir)],
    )

    assert result.exit_code == 1
    assert "comma-separated" in result.output
