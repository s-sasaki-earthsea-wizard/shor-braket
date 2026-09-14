# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Emulate the N = 15 QPU-oriented order-finding circuit on the approved devices.

For each device and oracle mode the logical circuit is placed and routed on the device graph,
lowered to native gates, wrapped in a verbatim box, validated, and run on the calibration-backed
local emulator. The report compares the noisy joint distribution of the count and work registers
with the ideal one. This is an observation of how much of the period-4 signal survives, not a
factoring claim: the swap-network oracle is specific to N = 15 and ``t = 2`` uses ``r <= 4``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
from braket.circuits import Circuit
from braket.circuits.serialization import IRType
from braket.devices import LocalSimulator
from braket.emulation.local_emulator import LocalEmulator
from numpy.typing import NDArray

from shor_braket.analysis.distribution import (
    expected_joint_probabilities,
    sampling_floor,
    total_variation_distance,
)
from shor_braket.classical.postprocess import recover_orders
from shor_braket.cost import QPU_CANDIDATES, qpu_cost_estimates
from shor_braket.devices.calibration import CalibrationSummary, summarize_snapshot
from shor_braket.devices.snapshot import DEFAULT_SNAPSHOT_DIR, DeviceSnapshot, load_snapshot
from shor_braket.gate.circuit_hash import circuit_hash
from shor_braket.quantum.compile import compile_to_native
from shor_braket.quantum.n15 import (
    MODULUS,
    ORACLE_MODES,
    WORK_QUBIT_COUNT,
    LogicalCircuit,
    build_n15_circuit,
)
from shor_braket.quantum.native import gate_family, verbatim
from shor_braket.quantum.routing import choose_layout
from shor_braket.runner.emulator import (
    BACKEND,
    PRICE_CHECKED_AT,
    build_emulator,
    validate_circuit,
)
from shor_braket.runner.local import run_local
from shor_braket.visualization.n15 import generate_n15_figures, n15_markdown

EXECUTION_CLASS = "local-emulator-n15"
PROBABILITY_TOLERANCE = 1e-9


def reorder_probabilities(
    probabilities: NDArray[np.float64], measured: Sequence[int], desired: Sequence[int]
) -> NDArray[np.float64]:
    """Reindex a probability vector from ascending ``measured`` order into ``desired`` order.

    The local simulators return probabilities and bit strings with qubits in ascending order
    regardless of the requested target order, so the register order is restored here.
    """
    if sorted(measured) != sorted(desired) or len(set(measured)) != len(measured):
        raise ValueError("measured and desired must be permutations of the same qubits")
    tensor = np.asarray(probabilities, dtype=np.float64).reshape([2] * len(measured))
    axes = [list(measured).index(qubit) for qubit in desired]
    return np.ascontiguousarray(np.transpose(tensor, axes)).reshape(-1)


def apply_readout_flips(
    probabilities: NDArray[np.float64], flip_rates: Sequence[float]
) -> NDArray[np.float64]:
    """Apply independent symmetric bit flips, one rate per qubit in register order."""
    tensor = np.asarray(probabilities, dtype=np.float64).reshape([2] * len(flip_rates))
    for axis, rate in enumerate(flip_rates):
        matrix = np.array([[1.0 - rate, rate], [rate, 1.0 - rate]])
        tensor = np.moveaxis(np.tensordot(matrix, tensor, axes=([1], [axis])), 0, axis)
    return tensor.reshape(-1)


def joint_from_counts(
    counts: Mapping[str, int], measured: Sequence[int], desired: Sequence[int]
) -> NDArray[np.float64]:
    """Probabilities of the ``desired`` register from counts over ``measured`` qubits.

    Qubits that were measured but are not part of the register (SWAP transit qubits) are
    marginalised out; bits are placed in ``desired`` order, most significant first.
    """
    shots = sum(counts.values())
    if shots <= 0:
        raise ValueError("measurement counts must contain at least one shot")
    positions = [list(measured).index(qubit) for qubit in desired]
    vector = np.zeros(1 << len(desired), dtype=np.float64)
    for state, count in counts.items():
        index = int("".join(state[position] for position in positions), 2)
        vector[index] += count / shots
    return vector


def order_recovery(
    counts: Mapping[str, int],
    measured: Sequence[int],
    count_physical_value_order: Sequence[int],
    *,
    base: int,
    count_qubit_count: int,
) -> dict[str, float]:
    """Per-shot rate at which the continued fraction of ``y`` yields the true order.

    The baseline is the same rate for a uniformly random ``y``; for ``t = 2`` it is 0.75, which
    is why this number is reported only to show that it is not evidence of anything.
    """
    true_order = 4 if base in (2, 7, 8, 13) else 2
    successes = 0
    shots = 0
    for state, count in counts.items():
        bits = {qubit: state[index] for index, qubit in enumerate(measured)}
        y = int("".join(bits[q] for q in count_physical_value_order), 2)
        if true_order in recover_orders(
            {y}, modulus=MODULUS, base=base, count_qubits=count_qubit_count
        ):
            successes += count
        shots += count
    scale = 1 << count_qubit_count
    baseline = (
        sum(
            1
            for y in range(scale)
            if true_order
            in recover_orders({y}, modulus=MODULUS, base=base, count_qubits=count_qubit_count)
        )
        / scale
    )
    return {"rate": successes / shots if shots else 0.0, "baseline_uniform_y": baseline}


def gate_noisy_probabilities(
    emulator: LocalEmulator, body: Circuit, measured: Sequence[int]
) -> NDArray[np.float64]:
    """Exact probabilities after the emulator's gate noise, before readout error.

    The emulator's result-type validator rejects ``Probability`` on all-to-all devices whose
    connectivity graph is empty, so the noise model is applied directly and the noisy circuit is
    run on the same density-matrix backend the emulator uses. Readout bit flips are attached to
    measurements only, so they are added classically by the caller.
    """
    noisy = emulator.noise_model.apply(body.copy())
    result = LocalSimulator(BACKEND).run(noisy.probability(target=list(measured)), shots=0).result()
    return np.asarray(result.values[0], dtype=np.float64)


def order_recovery_exact(
    count_probabilities: NDArray[np.float64], *, base: int, count_qubit_count: int
) -> float:
    """Order-recovery rate implied by an exact count-register distribution."""
    true_order = 4 if base in (2, 7, 8, 13) else 2
    rate = 0.0
    for y, probability in enumerate(count_probabilities):
        if true_order in recover_orders(
            {y}, modulus=MODULUS, base=base, count_qubits=count_qubit_count
        ):
            rate += float(probability)
    return rate


def _circuit_hash(program: Circuit) -> tuple[str, str]:
    """Hash the normalized IR and return the OpenQASM text for the record's audit trail."""
    return circuit_hash(program), str(program.to_ir(ir_type=IRType.OPENQASM).source)


def _count_marginal(vector: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.asarray(vector.reshape(-1, 1 << WORK_QUBIT_COUNT).sum(axis=1), dtype=np.float64)


def _support(vector: NDArray[np.float64], count_qubit_count: int) -> list[dict[str, Any]]:
    work_dimension = 1 << WORK_QUBIT_COUNT
    return [
        {
            "count_value": int(index) // work_dimension,
            "work_value": int(index) % work_dimension,
            "probability": float(vector[index]),
        }
        for index in np.flatnonzero(vector > PROBABILITY_TOLERANCE)
    ]


def emulate_n15_configuration(
    snapshot: DeviceSnapshot,
    summary: CalibrationSummary,
    *,
    oracle_mode: str,
    shots: int,
    base: int = 7,
    count_qubit_count: int = 2,
    max_permutations: int | None = None,
) -> dict[str, Any]:
    """Route, lower, validate and emulate one oracle mode on one device."""
    if shots < 1:
        raise ValueError("shots must be positive")
    logical: LogicalCircuit = build_n15_circuit(
        base=base, count_qubit_count=count_qubit_count, oracle_mode=oracle_mode
    )
    family = gate_family(summary.native_gate_set)
    logical_qubits = sorted([*logical.count_qubits_physical, *logical.work_qubits])
    routed = choose_layout(
        logical.circuit, summary, logical_qubits, max_permutations=max_permutations
    )
    native = compile_to_native(routed.circuit, family)
    program = verbatim(native.circuit)
    emulator = build_emulator(snapshot)
    accepted, error_type, message = validate_circuit(emulator, program)
    program_hash, source = _circuit_hash(program)

    # Register order for reading: count register in value order, then work (MSB first).
    desired_logical = [*logical.count_qubits, *logical.work_qubits]
    desired = [routed.final_layout[qubit] for qubit in desired_logical]
    measured = sorted(routed.final_layout.values())
    roles = {
        **{routed.final_layout[q]: f"c{index}" for index, q in enumerate(logical.count_qubits)},
        **{
            routed.final_layout[q]: f"w{WORK_QUBIT_COUNT - 1 - index}"
            for index, q in enumerate(logical.work_qubits)
        },
    }

    result: dict[str, Any] = {
        "device": snapshot.key,
        "oracle_mode": oracle_mode,
        "count_qubit_count": count_qubit_count,
        "base": base,
        "multipliers": list(logical.multipliers),
        "controlled_applications": [list(pair) for pair in logical.controlled_applications],
        "n_specific_decomposition": logical.n_specific_decomposition,
        "t_rationale": (
            "t = 2 represents the phases s/4 exactly and avoids identity multipliers; "
            "it uses the knowledge that r <= 4."
        ),
        "gate_family": family,
        "layout": {
            "initial": {str(k): v for k, v in routed.initial_layout.items()},
            "final": {str(k): v for k, v in routed.final_layout.items()},
            "physical_qubits": list(routed.physical_qubits),
            "used_qubits": list(routed.used_qubits),
            "transit_qubits": [q for q in routed.used_qubits if q not in routed.physical_qubits],
            "roles": {str(k): v for k, v in sorted(roles.items())},
            "swap_count": routed.swap_count,
            "error_budget": routed.error_budget,
        },
        "gates": {
            "logical_two_qubit": logical.two_qubit_gates,
            "routed_two_qubit": routed.two_qubit_gates,
            "native": native.gate_counts,
            "native_two_qubit": native.two_qubit_gates,
            "native_depth": native.circuit.depth,
            "instruction_count": len(native.circuit.instructions),
            "dropped_final_z": {str(k): v for k, v in native.dropped_final_z.items()},
        },
        "validation": {"accepted": accepted, "error_type": error_type, "message": message},
        "circuit_hash": program_hash,
        "openqasm": source,
    }
    if not accepted:
        return result

    expected = expected_joint_probabilities(
        modulus=MODULUS,
        base=base,
        count_qubit_count=count_qubit_count,
        work_qubit_count=WORK_QUBIT_COUNT,
    )
    uniform = np.full_like(expected, 1.0 / expected.size)
    ideal_raw = run_local(native.circuit.copy().probability(target=measured), shots=0)
    ideal = reorder_probabilities(np.asarray(ideal_raw.values[0]), measured, desired)
    gate_noisy_raw = gate_noisy_probabilities(emulator, native.circuit, measured)
    gate_noisy = reorder_probabilities(gate_noisy_raw, measured, desired)
    flip_rates = [summary.qubits[qubit].readout_flip_rate for qubit in desired]
    exact_noisy = apply_readout_flips(gate_noisy, flip_rates)

    sampled_result = emulator.run(program, shots=shots).result()
    counts = dict(sampled_result.measurement_counts)
    measured_qubits = [int(q) for q in sampled_result.measured_qubits]
    sampled = joint_from_counts(counts, measured_qubits, desired)

    support = expected > PROBABILITY_TOLERANCE
    tvd_ideal_uniform = total_variation_distance(expected, uniform)
    exact_tvd = total_variation_distance(exact_noisy, expected)
    sampled_tvd = total_variation_distance(sampled, expected)
    recovery = order_recovery(
        counts,
        measured_qubits,
        [routed.final_layout[q] for q in logical.count_qubits],
        base=base,
        count_qubit_count=count_qubit_count,
    )
    result.update(
        {
            "shots": shots,
            "ideal_check_tvd": total_variation_distance(ideal, expected),
            "metrics": {
                "gate_noise_only_tvd": total_variation_distance(gate_noisy, expected),
                "exact_tvd": exact_tvd,
                "sampled_tvd": sampled_tvd,
                "tvd_ideal_vs_uniform": tvd_ideal_uniform,
                "signal_fraction_exact": 1.0 - exact_tvd / tvd_ideal_uniform,
                "signal_fraction_sampled": 1.0 - sampled_tvd / tvd_ideal_uniform,
                "ideal_support_mass_exact": float(exact_noisy[support].sum()),
                "ideal_support_mass_sampled": float(sampled[support].sum()),
                "count_marginal_tvd_exact": total_variation_distance(
                    _count_marginal(exact_noisy), _count_marginal(expected)
                ),
                "order_recovery_rate": order_recovery_exact(
                    _count_marginal(exact_noisy), base=base, count_qubit_count=count_qubit_count
                ),
                "order_recovery_rate_sampled": recovery["rate"],
                "order_recovery_baseline_uniform_y": recovery["baseline_uniform_y"],
                "sampling_floor_estimate": sampling_floor(exact_noisy, shots),
            },
            "distributions": {
                "expected": expected.tolist(),
                "exact_noisy": exact_noisy.tolist(),
                "gate_noise_only": gate_noisy.tolist(),
                "sampled": sampled.tolist(),
                "exact_noisy_support": _support(exact_noisy, count_qubit_count),
            },
            "measurement_counts": dict(sorted(counts.items())),
            "measured_qubits": measured_qubits,
            "register_order_physical": desired,
        }
    )
    return result


def run_n15_emulation(
    *,
    device_keys: Sequence[str],
    oracle_modes: Sequence[str] = ORACLE_MODES,
    shots: int = 20_000,
    count_qubit_count: int = 2,
    output_dir: Path | None = Path("runs/raw"),
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    visualize: bool = True,
    max_permutations: int | None = None,
) -> dict[str, Any]:
    """Emulate every requested device / oracle combination and write one artifact directory."""
    unknown = [key for key in device_keys if key not in QPU_CANDIDATES]
    if unknown:
        raise ValueError(f"unknown device keys {unknown}; expected {sorted(QPU_CANDIDATES)}")
    bad_modes = [mode for mode in oracle_modes if mode not in ORACLE_MODES]
    if bad_modes:
        raise ValueError(f"unknown oracle modes {bad_modes}; expected {list(ORACLE_MODES)}")

    created_at = datetime.now(UTC)
    devices: dict[str, Any] = {}
    configurations: dict[str, Any] = {}
    for key in device_keys:
        snapshot = load_snapshot(key, snapshot_dir)
        summary = summarize_snapshot(snapshot)
        candidate = QPU_CANDIDATES[key]
        devices[key] = {
            "name": candidate.name,
            "arn": snapshot.arn,
            "provider": snapshot.provider,
            "gate_family": gate_family(summary.native_gate_set),
            "snapshot": {
                "fetched_at": snapshot.fetched_at,
                "calibration_updated_at": snapshot.calibration_updated_at,
                "capabilities_sha256": snapshot.capabilities_sha256,
            },
            "calibration": summary.to_dict(),
            "shots_range": list(summary.shots_range) if summary.shots_range else None,
            "future_qpu_cost": next(
                row for row in qpu_cost_estimates(shots) if row["device"] == key
            ),
        }
        for mode in oracle_modes:
            configurations[f"{key}/{mode}"] = emulate_n15_configuration(
                snapshot,
                summary,
                oracle_mode=mode,
                shots=shots,
                count_qubit_count=count_qubit_count,
                max_permutations=max_permutations,
            )

    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "execution": {
            "backend": "braket_dm",
            "class": EXECUTION_CLASS,
            "aws_requests": False,
            "estimated_cost_usd": "0.00",
            "shots": shots,
        },
        "problem": {
            "modulus": MODULUS,
            "base": 7,
            "count_qubits": count_qubit_count,
            "work_qubits": WORK_QUBIT_COUNT,
            "classical_order": 4,
            "claim": (
                "Observation of period-4 signal survival under calibration noise; not a factoring "
                "claim. The oracle is the N = 15 swap network and t = 2 uses r <= 4."
            ),
        },
        "devices": devices,
        "configurations": configurations,
        "qpu_gate": {
            "validated_record_issued": False,
            "qpu_eligible": False,
            "reason": "Validated records and the submission gate are a separate branch (issue #8).",
        },
        "price_checked_at": PRICE_CHECKED_AT,
        "environment": {
            "amazon_braket_sdk": version("amazon-braket-sdk"),
            "amazon_braket_default_simulator": version("amazon-braket-default-simulator"),
            "numpy": version("numpy"),
            "matplotlib": version("matplotlib"),
            "networkx": version("networkx"),
        },
    }

    if output_dir is not None:
        artifact_dir = output_dir / f"n15-emulation-{created_at:%Y%m%dT%H%M%S%fZ}"
        artifact_dir.mkdir(parents=True, exist_ok=False)
        circuit_dir = artifact_dir / "circuits"
        circuit_dir.mkdir()
        for name, configuration in configurations.items():
            source = configuration.pop("openqasm")
            path = circuit_dir / f"{name.replace('/', '-')}.qasm"
            path.write_text(source, encoding="utf-8")
            configuration["openqasm_path"] = str(path.relative_to(artifact_dir))
        report["artifact_path"] = str(artifact_dir / "result.json")
        if visualize:
            report["visualizations"] = generate_n15_figures(report, artifact_dir)
            (artifact_dir / "report.md").write_text(n15_markdown(report), "utf-8")
            report["report_markdown"] = "report.md"
        (artifact_dir / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return report
