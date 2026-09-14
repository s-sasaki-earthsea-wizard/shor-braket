# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Emulate the iterative (feed-forward) N = 15 circuit next to the standard one.

For each device and oracle mode the feed-forward circuit is placed and routed on the device
graph, lowered to native gates, wrapped in a verbatim box and validated by the calibration-backed
``LocalEmulator``. The noisy distribution is computed exactly by rewriting the noisy circuit with
the deferred-measurement principle and reading the ``Probability`` result type on the
density-matrix backend; finite-shot samples are multinomial draws from that distribution.

Two things about the SDK (1.127.0, default simulator 1.40.1) make that necessary. The emulator's
noise model attaches nothing to ``measure_ff`` and ``cc_prx``, so a readout bit flip before each
``measure_ff`` and a one-qubit depolarizing channel after each ``cc_prx`` are added here from the
same calibration numbers. And the shot-by-shot ("branched") simulation the simulator uses for
feed-forward circuits drops most noise channels (one-qubit depolarizing anywhere, bit flips after
a ``measure_ff``), so ``LocalEmulator.run`` on such a circuit returns a nearly noise-free sample.
Both SDK runs are still executed with a small shot count and recorded as diagnostics so the
discrepancy stays visible. Devices without feed-forward are reported as rejected. The standard
circuit of ``runner/n15.py`` is run for the same device and oracle so both methods sit in one
table, and a Monte Carlo of finite-shot samples records what the TVD and signal-fraction
estimators do at each shot count.

None of this is a factoring claim: the oracle is the N = 15 swap network and ``t = 2`` uses
``r <= 4``. What is measured is how much of the period-4 signal survives on each method.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from math import exp
from pathlib import Path
from typing import Any

import numpy as np
from braket.circuits import Circuit, Instruction
from braket.circuits.noises import BitFlip, Depolarizing
from braket.circuits.serialization import IRType
from braket.devices import LocalSimulator
from braket.emulation.local_emulator import LocalEmulator
from braket.experimental_capabilities.iqm.classical_control import CCPRx, MeasureFF
from numpy.typing import NDArray

from shor_braket.analysis.distribution import (
    expected_joint_probabilities,
    hellinger_fidelity,
    sampling_floor,
    sampling_sweep,
    signal_fraction,
    support_signal_fraction,
    total_variation_distance,
)
from shor_braket.cost import QPU_CANDIDATES, qpu_cost_estimates
from shor_braket.devices.calibration import CalibrationSummary, summarize_snapshot
from shor_braket.devices.snapshot import DEFAULT_SNAPSHOT_DIR, DeviceSnapshot, load_snapshot
from shor_braket.quantum.compile import compile_to_native
from shor_braket.quantum.feedforward import (
    UNCHECKED_CONSTRAINTS,
    deferred_measurement_circuit,
    feed_forward_violations,
    supports_feed_forward,
)
from shor_braket.quantum.n15 import MODULUS, ORACLE_MODES, WORK_QUBIT_COUNT
from shor_braket.quantum.n15_iterative import (
    METHOD,
    IterativeCircuit,
    build_n15_iterative_circuit,
)
from shor_braket.quantum.native import gate_family, verbatim
from shor_braket.quantum.routing import choose_layout
from shor_braket.runner.emulator import (
    BACKEND,
    PRICE_CHECKED_AT,
    build_emulator,
    validate_circuit,
)
from shor_braket.runner.n15 import (
    PROBABILITY_TOLERANCE,
    apply_readout_flips,
    emulate_n15_configuration,
    joint_from_counts,
    order_recovery_exact,
    reorder_probabilities,
)
from shor_braket.visualization.iterative import generate_iterative_figures, iterative_markdown

EXECUTION_CLASS = "local-emulator-n15-iterative"
METHOD_STANDARD = "standard-qpe"
METHOD_ITERATIVE = METHOD
DEFAULT_SWEEP_SHOTS: tuple[int, ...] = (500, 1000, 2000, 4000, 10_000, 20_000)
FEED_FORWARD_NOISE_NOTE = (
    "The SDK noise model attaches no channel to measure_ff or cc_prx; a readout bit flip "
    "(state flip before the measurement) and a one-qubit depolarizing channel were added from "
    "the same calibration. Idle errors while the classical controller decides are not modelled."
)
BRANCHED_SIMULATION_NOTE = (
    "The simulator's shot-by-shot simulation of feed-forward circuits drops most noise channels "
    "(one-qubit depolarizing anywhere, bit flips after a measure_ff; checked with minimal "
    "circuits on amazon-braket-default-simulator 1.40.1). These counts are a diagnostic only; "
    "the metrics use the exact deferred-measurement distribution and multinomial samples of it."
)


def add_feed_forward_noise(circuit: Circuit, summary: CalibrationSummary) -> Circuit:
    """Insert the channels the emulator's noise model does not attach to feed-forward ops."""
    result = Circuit()
    for instruction in circuit.instructions:
        operator = instruction.operator
        if isinstance(operator, MeasureFF):
            qubit = int(instruction.target[0])
            result.add_instruction(
                Instruction(BitFlip(summary.qubits[qubit].readout_flip_rate), qubit)
            )
            result.add_instruction(instruction)
        elif isinstance(operator, CCPRx):
            qubit = int(instruction.target[0])
            result.add_instruction(instruction)
            result.add_instruction(
                Instruction(Depolarizing(summary.qubits[qubit].depolarizing_rate), qubit)
            )
        else:
            result.add_instruction(instruction)
    for result_type in circuit.result_types:
        result.add_result_type(result_type)
    return result


def noisy_feed_forward_circuit(
    emulator: LocalEmulator,
    summary: CalibrationSummary,
    body: Circuit,
    measured: Sequence[int],
    *,
    readout: bool = True,
) -> Circuit:
    """Apply the emulator's noise model plus the feed-forward channels to a native circuit.

    With ``readout`` the measured qubits get explicit ``measure`` instructions first, so the
    noise model attaches its readout bit flips to them.
    """
    circuit = body.copy()
    if readout:
        for qubit in measured:
            circuit.measure(qubit)
    return add_feed_forward_noise(emulator.noise_model.apply(circuit), summary)


def exact_probabilities(
    circuit: Circuit, measured: Sequence[int], *, first_ancilla: int, backend: str = BACKEND
) -> NDArray[np.float64]:
    """Exact output distribution of a feed-forward circuit via deferred measurement.

    Qubits outside ``measured`` (SWAP transit qubits, ancillas) are marginalised by the
    ``Probability`` result type. Use ``braket_sv`` for noise-free circuits.
    """
    deferred = deferred_measurement_circuit(circuit, first_ancilla)
    program = deferred.circuit.probability(target=list(measured))
    result = LocalSimulator(backend).run(program, shots=0).result()
    return np.asarray(result.values[0], dtype=np.float64)


def _circuit_hash(program: Circuit) -> tuple[str, str]:
    source = program.to_ir(ir_type=IRType.OPENQASM).source
    return f"sha256:{hashlib.sha256(source.encode()).hexdigest()}", str(source)


def _count_marginal(vector: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.asarray(vector.reshape(-1, 1 << WORK_QUBIT_COUNT).sum(axis=1), dtype=np.float64)


def _support(vector: NDArray[np.float64]) -> list[dict[str, Any]]:
    work_dimension = 1 << WORK_QUBIT_COUNT
    return [
        {
            "count_value": int(index) // work_dimension,
            "work_value": int(index) % work_dimension,
            "probability": float(vector[index]),
        }
        for index in np.flatnonzero(vector > PROBABILITY_TOLERANCE)
    ]


def _scores(
    exact: NDArray[np.float64],
    sampled: NDArray[np.float64],
    expected: NDArray[np.float64],
    *,
    shots: int,
    error_budget: float,
) -> dict[str, float]:
    """Metrics shared by both methods so they can be compared column by column."""
    uniform = np.full_like(expected, 1.0 / expected.size)
    support = expected > PROBABILITY_TOLERANCE
    support_fraction = float(support.mean())
    tvd_ideal_uniform = total_variation_distance(expected, uniform)
    exact_tvd = total_variation_distance(exact, expected)
    sampled_tvd = total_variation_distance(sampled, expected)
    exact_mass = float(exact[support].sum())
    sampled_mass = float(sampled[support].sum())
    return {
        "exact_tvd": exact_tvd,
        "sampled_tvd": sampled_tvd,
        "tvd_ideal_vs_uniform": tvd_ideal_uniform,
        "signal_fraction_exact": signal_fraction(exact_tvd, tvd_ideal_uniform),
        "signal_fraction_sampled": signal_fraction(sampled_tvd, tvd_ideal_uniform),
        "ideal_support_mass_exact": exact_mass,
        "ideal_support_mass_sampled": sampled_mass,
        "support_signal_fraction_exact": support_signal_fraction(exact_mass, support_fraction),
        "support_signal_fraction_sampled": support_signal_fraction(sampled_mass, support_fraction),
        "hellinger_fidelity_exact": hellinger_fidelity(exact, expected),
        "predicted_signal_fraction": exp(-error_budget),
        "sampling_floor_formula": sampling_floor(exact, shots),
    }


def emulate_iterative_configuration(
    snapshot: DeviceSnapshot,
    summary: CalibrationSummary,
    *,
    oracle_mode: str,
    shots: int,
    base: int = 7,
    count_qubit_count: int = 2,
    max_permutations: int | None = None,
    emulator_check_shots: int = 200,
    seed: int = 0,
) -> dict[str, Any]:
    """Route, lower, validate and emulate the iterative circuit for one oracle on one device."""
    if shots < 1:
        raise ValueError("shots must be positive")
    logical: IterativeCircuit = build_n15_iterative_circuit(
        base=base, count_qubit_count=count_qubit_count, oracle_mode=oracle_mode
    )
    result: dict[str, Any] = {
        "device": snapshot.key,
        "oracle_mode": oracle_mode,
        "method": METHOD_ITERATIVE,
        "count_qubit_count": count_qubit_count,
        "base": base,
        "multipliers": list(logical.multipliers),
        "controlled_applications": [list(pair) for pair in logical.controlled_applications],
        "rounds": [
            {
                "index": r.index,
                "exponent": r.exponent,
                "multiplier": r.multiplier,
                "feedback_key": r.feedback_key,
                "corrections": [list(c) for c in r.corrections],
            }
            for r in logical.rounds
        ],
        "n_specific_decomposition": logical.n_specific_decomposition,
        "t_rationale": (
            "t = 2 represents the phases s/4 exactly and avoids identity multipliers; "
            "it uses the knowledge that r <= 4."
        ),
        "feed_forward": {
            "supported": supports_feed_forward(summary.native_gate_set),
            "logical_counts": logical.feed_forward,
            "violations": feed_forward_violations(logical.circuit),
            "unchecked_constraints": list(UNCHECKED_CONSTRAINTS),
            "record_qubits": (
                "mid-circuit outcomes are not returned by the hardware, so each is copied into a "
                "record qubit with cc_prx; the record qubit needs no coupler"
            ),
            "noise_added": FEED_FORWARD_NOISE_NOTE,
        },
    }
    if not result["feed_forward"]["supported"]:
        result["validation"] = {
            "accepted": False,
            "error_type": "FeedForwardUnsupported",
            "message": (
                f"native gate set {list(summary.native_gate_set)} has no measure_ff / cc_prx"
            ),
        }
        return result

    family = gate_family(summary.native_gate_set)
    routed = choose_layout(
        logical.circuit,
        summary,
        sorted(logical.core_qubits),
        max_permutations=max_permutations,
        detached=logical.record_qubits,
    )
    native = compile_to_native(routed.circuit, family)
    program = verbatim(native.circuit)
    emulator = build_emulator(snapshot)
    accepted, error_type, message = validate_circuit(emulator, program)
    program_hash, source = _circuit_hash(program)

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
    result.update(
        {
            "gate_family": family,
            "layout": {
                "initial": {str(k): v for k, v in routed.initial_layout.items()},
                "final": {str(k): v for k, v in routed.final_layout.items()},
                "physical_qubits": list(routed.physical_qubits),
                "used_qubits": list(routed.used_qubits),
                "transit_qubits": [
                    q for q in routed.used_qubits if q not in routed.physical_qubits
                ],
                "roles": {str(k): v for k, v in sorted(roles.items())},
                "role_notes": {
                    "c0": "count qubit, reused every round; holds the last round's bit",
                    **{
                        f"c{index + 1}": f"record of round {len(logical.record_qubits) - 1 - index}"
                        for index in range(len(logical.record_qubits))
                    },
                },
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
    )
    if not accepted:
        return result

    expected = expected_joint_probabilities(
        modulus=MODULUS,
        base=base,
        count_qubit_count=count_qubit_count,
        work_qubit_count=WORK_QUBIT_COUNT,
    )
    first_ancilla = max(summary.qubit_labels) + 1
    ideal_raw = exact_probabilities(
        native.circuit, measured, first_ancilla=first_ancilla, backend="braket_sv"
    )
    ideal = reorder_probabilities(ideal_raw, measured, desired)
    noisy = noisy_feed_forward_circuit(emulator, summary, native.circuit, measured)
    exact_raw = exact_probabilities(noisy, measured, first_ancilla=first_ancilla)
    exact = reorder_probabilities(exact_raw, measured, desired)
    no_readout = noisy_feed_forward_circuit(
        emulator, summary, native.circuit, measured, readout=False
    )
    gate_noisy = reorder_probabilities(
        exact_probabilities(no_readout, measured, first_ancilla=first_ancilla), measured, desired
    )

    support = expected > PROBABILITY_TOLERANCE
    rng = np.random.default_rng(seed)
    sampled = rng.multinomial(shots, np.clip(exact, 0.0, None) / exact.sum()) / shots

    diagnostics: dict[str, Any] = {"shots": emulator_check_shots, "note": BRANCHED_SIMULATION_NOTE}
    if emulator_check_shots > 0:
        readout_only = apply_readout_flips(
            expected, [summary.qubits[qubit].readout_flip_rate for qubit in desired]
        )
        diagnostics["readout_only_ideal_support_mass"] = float(readout_only[support].sum())
        for label, runner, circuit in (
            ("braket_dm_feed_forward", LocalSimulator(BACKEND).run, noisy),
            ("local_emulator", emulator.run, program),
        ):
            run_result = runner(circuit, shots=emulator_check_shots).result()
            run_counts = dict(run_result.measurement_counts)
            run_joint = joint_from_counts(
                run_counts, [int(q) for q in run_result.measured_qubits], desired
            )
            diagnostics[label] = {
                "ideal_support_mass": float(run_joint[support].sum()),
                "measurement_counts": dict(sorted(run_counts.items())),
            }

    metrics = _scores(exact, sampled, expected, shots=shots, error_budget=routed.error_budget)
    metrics.update(
        {
            "gate_noise_only_tvd": total_variation_distance(gate_noisy, expected),
            "count_marginal_tvd_exact": total_variation_distance(
                _count_marginal(exact), _count_marginal(expected)
            ),
            "order_recovery_rate": order_recovery_exact(
                _count_marginal(exact), base=base, count_qubit_count=count_qubit_count
            ),
            "order_recovery_rate_sampled": order_recovery_exact(
                _count_marginal(sampled), base=base, count_qubit_count=count_qubit_count
            ),
            "order_recovery_baseline_uniform_y": _uniform_recovery_baseline(
                base=base, count_qubit_count=count_qubit_count
            ),
        }
    )
    result.update(
        {
            "shots": shots,
            "sampling": "multinomial draw from the exact distribution (seeded)",
            "seed": seed,
            "ideal_check_tvd": total_variation_distance(ideal, expected),
            "metrics": metrics,
            "sdk_run_diagnostics": diagnostics,
            "distributions": {
                "expected": expected.tolist(),
                "exact_noisy": exact.tolist(),
                "gate_noise_only": gate_noisy.tolist(),
                "sampled": sampled.tolist(),
                "exact_noisy_support": _support(exact),
            },
            "measured_qubits": measured,
            "register_order_physical": desired,
        }
    )
    return result


def _uniform_recovery_baseline(*, base: int, count_qubit_count: int) -> float:
    """Order-recovery rate of a uniformly random count value (0.75 for t = 2)."""
    scale = 1 << count_qubit_count
    return order_recovery_exact(
        np.full(scale, 1.0 / scale), base=base, count_qubit_count=count_qubit_count
    )


def standard_configuration(
    snapshot: DeviceSnapshot,
    summary: CalibrationSummary,
    *,
    oracle_mode: str,
    shots: int,
    count_qubit_count: int = 2,
    max_permutations: int | None = None,
) -> dict[str, Any]:
    """Run the standard circuit and add the shared scores so both methods line up."""
    config = emulate_n15_configuration(
        snapshot,
        summary,
        oracle_mode=oracle_mode,
        shots=shots,
        count_qubit_count=count_qubit_count,
        max_permutations=max_permutations,
    )
    config["method"] = METHOD_STANDARD
    if config["validation"]["accepted"]:
        distributions = config["distributions"]
        scores = _scores(
            np.asarray(distributions["exact_noisy"]),
            np.asarray(distributions["sampled"]),
            np.asarray(distributions["expected"]),
            shots=shots,
            error_budget=float(config["layout"]["error_budget"]),
        )
        config["metrics"] = {**config["metrics"], **scores}
    return config


def run_iterative_emulation(
    *,
    device_keys: Sequence[str],
    oracle_modes: Sequence[str] = ORACLE_MODES,
    shots: int = 4000,
    count_qubit_count: int = 2,
    sweep_shots: Sequence[int] = DEFAULT_SWEEP_SHOTS,
    repetitions: int = 200,
    include_standard: bool = True,
    output_dir: Path | None = Path("runs/raw"),
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    visualize: bool = True,
    max_permutations: int | None = None,
    emulator_check_shots: int = 200,
) -> dict[str, Any]:
    """Emulate both methods on every requested device / oracle and write one artifact directory."""
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
            "feed_forward": supports_feed_forward(summary.native_gate_set),
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
            configurations[f"{key}/{mode}/{METHOD_ITERATIVE}"] = emulate_iterative_configuration(
                snapshot,
                summary,
                oracle_mode=mode,
                shots=shots,
                count_qubit_count=count_qubit_count,
                max_permutations=max_permutations,
                emulator_check_shots=emulator_check_shots,
            )
            if include_standard:
                configurations[f"{key}/{mode}/{METHOD_STANDARD}"] = standard_configuration(
                    snapshot,
                    summary,
                    oracle_mode=mode,
                    shots=shots,
                    count_qubit_count=count_qubit_count,
                    max_permutations=max_permutations,
                )

    expected = expected_joint_probabilities(
        modulus=MODULUS,
        base=7,
        count_qubit_count=count_qubit_count,
        work_qubit_count=WORK_QUBIT_COUNT,
    )
    sampling: dict[str, Any] = {
        "shots_list": list(sweep_shots),
        "ideal": sampling_sweep(
            expected, expected, shots_list=sweep_shots, repetitions=repetitions
        ),
    }
    for name, configuration in configurations.items():
        if configuration["validation"]["accepted"]:
            sampling[name] = sampling_sweep(
                np.asarray(configuration["distributions"]["exact_noisy"]),
                expected,
                shots_list=sweep_shots,
                repetitions=repetitions,
            )

    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "execution": {
            "backend": BACKEND,
            "class": EXECUTION_CLASS,
            "aws_requests": False,
            "estimated_cost_usd": "0.00",
            "shots": shots,
            "emulator_check_shots": emulator_check_shots,
        },
        "problem": {
            "modulus": MODULUS,
            "base": 7,
            "count_qubits": count_qubit_count,
            "work_qubits": WORK_QUBIT_COUNT,
            "classical_order": 4,
            "claim": (
                "Observation of period-4 signal survival under calibration noise for the "
                "standard and the iterative (feed-forward) circuit; not a factoring claim. "
                "The oracle is the N = 15 swap network and t = 2 uses r <= 4."
            ),
        },
        "methods": {
            METHOD_STANDARD: "t count qubits, inverse QFT without final swaps, one measurement",
            METHOD_ITERATIVE: (
                "one count qubit reused over t rounds (measure_ff + cc_prx reset), phase "
                "corrections as cc_prx pairs, outcomes copied into record qubits"
            ),
        },
        "devices": devices,
        "configurations": configurations,
        "sampling": sampling,
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
        },
    }

    if output_dir is not None:
        artifact_dir = output_dir / f"n15-iterative-{created_at:%Y%m%dT%H%M%S%fZ}"
        artifact_dir.mkdir(parents=True, exist_ok=False)
        circuit_dir = artifact_dir / "circuits"
        circuit_dir.mkdir()
        for name, configuration in configurations.items():
            if "openqasm" not in configuration:
                continue
            source = configuration.pop("openqasm")
            path = circuit_dir / f"{name.replace('/', '-')}.qasm"
            path.write_text(source, encoding="utf-8")
            configuration["openqasm_path"] = str(path.relative_to(artifact_dir))
        report["artifact_path"] = str(artifact_dir / "result.json")
        if visualize:
            report["visualizations"] = generate_iterative_figures(report, artifact_dir)
            (artifact_dir / "report.md").write_text(iterative_markdown(report), "utf-8")
            report["report_markdown"] = "report.md"
        (artifact_dir / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return report


def configuration_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten the configurations into comparable rows (accepted ones carry metrics)."""
    rows: list[dict[str, Any]] = []
    for name, configuration in report["configurations"].items():
        device, mode, method = name.split("/")
        row: dict[str, Any] = {
            "key": name,
            "device": device,
            "oracle_mode": mode,
            "method": method,
            "accepted": configuration["validation"]["accepted"],
        }
        if "layout" in configuration:
            row["error_budget"] = configuration["layout"]["error_budget"]
            row["swap_count"] = configuration["layout"]["swap_count"]
            row["native_two_qubit"] = configuration["gates"]["native_two_qubit"]
        if configuration["validation"]["accepted"]:
            row.update(configuration["metrics"])
        rows.append(row)
    return rows
