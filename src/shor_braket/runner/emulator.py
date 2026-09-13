# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Validate and run native circuits on calibration-backed local emulators.

This is the offline half of the future QPU gate: a ``LocalEmulator`` is built from a committed
device snapshot, circuits are checked against the device's verbatim rules, and accepted circuits
are simulated with the calibration-derived noise model. No AWS request is made and no validated
record is issued; the circuits here are smoke tests, not Shor circuits.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
from braket.circuits import Circuit
from braket.emulation.local_emulator import LocalEmulator
from numpy.typing import NDArray

from shor_braket.analysis.distribution import (
    sampled_probability_vector,
    total_variation_distance,
)
from shor_braket.cost import QPU_CANDIDATES, qpu_cost_estimates
from shor_braket.devices.calibration import CalibrationSummary, summarize_snapshot
from shor_braket.devices.snapshot import DEFAULT_SNAPSHOT_DIR, DeviceSnapshot, load_snapshot
from shor_braket.quantum.native import (
    bell_circuit,
    cnot_ladder_circuit,
    gate_family,
    ghz_circuit,
    two_qubit_gate_count,
    verbatim,
)
from shor_braket.quantum.reference import build_reference_circuit
from shor_braket.runner.local import run_local
from shor_braket.visualization.emulation import emulation_markdown, generate_emulation_figures

EXECUTION_CLASS = "local-emulator"
BACKEND = "braket_dm"
DEFAULT_SWEEP_PAIRS: tuple[int, ...] = (0, 2, 5, 10, 20, 40)
PRICE_CHECKED_AT = "2026-09-14"
MESSAGE_LIMIT = 300


@dataclass(frozen=True)
class ValidationOutcome:
    """Result of passing one circuit through the emulator's validators."""

    name: str
    description: str
    qubits: list[int]
    verbatim: bool
    accepted: bool
    error_type: str | None
    message: str


def build_emulator(snapshot: DeviceSnapshot) -> LocalEmulator:
    """Create the SDK emulator from a snapshot; no network access is involved."""
    return LocalEmulator.from_json(snapshot.capabilities_json)


def validate_circuit(emulator: LocalEmulator, circuit: Circuit) -> tuple[bool, str | None, str]:
    """Run only the validation passes and report the first failure, if any."""
    try:
        emulator.validate(circuit)
    except Exception as error:  # the SDK raises several exception types
        return False, type(error).__name__, str(error)[:MESSAGE_LIMIT]
    return True, None, ""


def noise_channel_counts(emulator: LocalEmulator) -> dict[str, int]:
    """Count the noise channels the emulator attached, by channel type."""
    counts: dict[str, int] = {}
    for instruction in emulator.noise_model.instructions:
        name = type(instruction.noise).__name__
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _ideal_probabilities(body: Circuit, qubits: list[int]) -> NDArray[np.float64]:
    result = run_local(body.copy().probability(target=qubits), shots=0)
    return np.asarray(result.values[0], dtype=np.float64)


def run_native_circuit(
    emulator: LocalEmulator, body: Circuit, qubits: list[int], *, shots: int
) -> dict[str, Any]:
    """Run a native circuit noiselessly and on the emulator, and compare the distributions.

    Args:
        emulator: Calibration-backed emulator.
        body: Native-gate circuit without a verbatim box.
        qubits: Physical qubits the circuit touches, in ascending order.
        shots: Emulator shots.

    Returns:
        Ideal support, emulated counts, total variation distance and the probability mass the
        emulator left on the ideal support.
    """
    if shots < 1:
        raise ValueError("shots must be positive")
    ordered = sorted(qubits)
    ideal = _ideal_probabilities(body, ordered)
    result = emulator.run(verbatim(body), shots=shots).result()
    counts = dict(result.measurement_counts)
    sampled = sampled_probability_vector(counts, total_qubit_count=len(ordered))
    support = ideal > 1e-9
    width = len(ordered)
    return {
        "qubits": ordered,
        "measured_qubits": [int(q) for q in result.measured_qubits],
        "instruction_count": len(body.instructions),
        "two_qubit_gates": two_qubit_gate_count(body),
        "shots": shots,
        "ideal": {
            format(index, f"0{width}b"): float(p) for index, p in enumerate(ideal) if p > 1e-9
        },
        "measurement_counts": dict(sorted(counts.items())),
        "tvd": total_variation_distance(sampled, ideal),
        "ideal_support_mass": float(sampled[support].sum()),
    }


def validation_matrix(
    emulator: LocalEmulator, summary: CalibrationSummary, family: str
) -> list[ValidationOutcome]:
    """Check representative circuits against the device's verbatim rules."""
    labels = summary.qubit_labels
    first, second = labels[0], labels[1]
    best = summary.best_edge()
    path = summary.best_path(3)
    beyond = (max(labels) + 1, max(labels) + 2)
    reference = build_reference_circuit(modulus=15, base=7, count_qubit_count=8).circuit
    non_adjacent = summary.non_adjacent_pair()

    rows: list[tuple[str, str, list[int], Circuit, bool]] = [
        (
            "reference-n15-t8",
            "Dense-matrix Shor reference circuit (N=15, a=7, t=8) without a verbatim box",
            list(range(reference.qubit_count)),
            reference,
            False,
        ),
        (
            "reference-n15-t8-verbatim",
            "Same reference circuit inside a verbatim box; dense unitaries are not native",
            list(range(reference.qubit_count)),
            verbatim(reference),
            True,
        ),
        (
            "textbook-bell",
            "H and CNOT from the SDK without a verbatim box",
            [first, second],
            Circuit().h(first).cnot(first, second),
            False,
        ),
        (
            "textbook-bell-verbatim",
            "H and CNOT inside a verbatim box; neither is a native gate",
            [first, second],
            verbatim(Circuit().h(first).cnot(first, second)),
            True,
        ),
        (
            "native-bell-no-verbatim",
            "Native gates on the best coupler but no verbatim box",
            list(best),
            bell_circuit(best, family),
            False,
        ),
        (
            "native-bell-unknown-qubit",
            "Native Bell on qubit labels the device does not have",
            list(beyond),
            verbatim(bell_circuit(beyond, family)),
            True,
        ),
    ]
    if non_adjacent is not None:
        rows.append(
            (
                "native-bell-non-adjacent",
                "Native Bell on two qubits that share no coupler",
                list(non_adjacent),
                verbatim(bell_circuit(non_adjacent, family)),
                True,
            )
        )
    else:
        far = (labels[0], labels[-1])
        rows.append(
            (
                "native-bell-non-adjacent",
                "Native Bell on the two most distant labels; the device is all-to-all",
                list(far),
                verbatim(bell_circuit(far, family)),
                True,
            )
        )
    rows.extend(
        [
            (
                "native-bell-best-edge",
                "Native Bell on the coupler with the best two-qubit fidelity",
                list(best),
                verbatim(bell_circuit(best, family)),
                True,
            ),
            (
                "native-ghz3-best-path",
                "Native GHZ on the three-qubit path with the best coupler product",
                list(path),
                verbatim(ghz_circuit(path, family)),
                True,
            ),
        ]
    )

    outcomes: list[ValidationOutcome] = []
    for name, description, qubits, circuit, is_verbatim in rows:
        accepted, error_type, message = validate_circuit(emulator, circuit)
        outcomes.append(
            ValidationOutcome(
                name=name,
                description=description,
                qubits=qubits,
                verbatim=is_verbatim,
                accepted=accepted,
                error_type=error_type,
                message=message,
            )
        )
    return outcomes


def depth_sweep(
    emulator: LocalEmulator,
    summary: CalibrationSummary,
    family: str,
    *,
    shots: int,
    pairs: tuple[int, ...] = DEFAULT_SWEEP_PAIRS,
) -> dict[str, Any]:
    """Measure how the ideal-support mass of a Bell pair decays with two-qubit gate count."""
    sweep: dict[str, Any] = {"pairs": list(pairs), "edges": {}}
    edges = {"best": summary.best_edge(), "worst": summary.worst_edge()}
    for label, edge in edges.items():
        calibration = summary.edge(edge)
        points = []
        for count in pairs:
            body = cnot_ladder_circuit(edge, family, count)
            run = run_native_circuit(emulator, body, list(edge), shots=shots)
            points.append(
                {
                    "cnot_pairs": count,
                    "two_qubit_gates": run["two_qubit_gates"],
                    "tvd": run["tvd"],
                    "ideal_support_mass": run["ideal_support_mass"],
                }
            )
        one_qubit = [summary.qubits[qubit].rb_fidelity for qubit in edge]
        sweep["edges"][label] = {
            "qubits": list(edge),
            "two_qubit_fidelity": calibration.fidelity if calibration else None,
            "one_qubit_rb": one_qubit,
            "one_qubit_rb_min": min(one_qubit),
            "readout_fidelity": [summary.qubits[qubit].readout_fidelity for qubit in edge],
            "points": points,
        }
    sweep["model"] = "0.5 + 0.5 * f_2q ** two_qubit_gates (two-qubit gate fidelity only)"
    return sweep


def run_emulator_report(
    *,
    device_key: str,
    shots: int = 2000,
    sweep_shots: int = 4000,
    output_dir: Path | None = Path("runs/raw"),
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    visualize: bool = True,
) -> dict[str, Any]:
    """Build the emulator for one approved device and record what it accepts and how it degrades.

    Args:
        device_key: Logical device name (``garnet``, ``emerald`` or ``ibex``).
        shots: Shots for the Bell and GHZ runs.
        sweep_shots: Shots per point of the depth sweep.
        output_dir: Parent of the artifact directory, or None to skip writing.
        snapshot_dir: Directory holding committed device snapshots.
        visualize: Whether to render figures and a Markdown report.

    Returns:
        The report dictionary; also written as ``result.json`` when ``output_dir`` is set.
    """
    if device_key not in QPU_CANDIDATES:
        raise ValueError(
            f"{device_key!r} has no local emulator target; choose one of {sorted(QPU_CANDIDATES)}"
        )
    if shots < 1 or sweep_shots < 1:
        raise ValueError("shots and sweep_shots must be positive")

    snapshot = load_snapshot(device_key, snapshot_dir)
    summary = summarize_snapshot(snapshot)
    family = gate_family(summary.native_gate_set)
    emulator = build_emulator(snapshot)
    created_at = datetime.now(UTC)

    outcomes = validation_matrix(emulator, summary, family)
    best = summary.best_edge()
    worst = summary.worst_edge()
    path = summary.best_path(3)
    runs = {
        "native-bell-best-edge": run_native_circuit(
            emulator, bell_circuit(best, family), list(best), shots=shots
        ),
        "native-bell-worst-edge": run_native_circuit(
            emulator, bell_circuit(worst, family), list(worst), shots=shots
        ),
        "native-ghz3-best-path": run_native_circuit(
            emulator, ghz_circuit(path, family), list(path), shots=shots
        ),
    }
    sweep = depth_sweep(emulator, summary, family, shots=sweep_shots)
    candidate = QPU_CANDIDATES[device_key]
    cost = next(row for row in qpu_cost_estimates(shots) if row["device"] == device_key)

    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "execution": {
            "backend": BACKEND,
            "class": EXECUTION_CLASS,
            "aws_requests": False,
            "estimated_cost_usd": "0.00",
            "shots": shots,
            "sweep_shots": sweep_shots,
        },
        "device": {
            "key": device_key,
            "name": candidate.name,
            "provider": snapshot.provider,
            "arn": snapshot.arn,
            "region": candidate.region,
            "status_at_fetch": snapshot.status_at_fetch,
            "snapshot": {
                "path": str(snapshot_dir / f"{device_key}.json"),
                "fetched_at": snapshot.fetched_at,
                "calibration_updated_at": snapshot.calibration_updated_at,
                "capabilities_sha256": snapshot.capabilities_sha256,
            },
            "gate_family": family,
            "calibration": summary.to_dict(),
            "noise_model": {
                "channels": noise_channel_counts(emulator),
                "backend": BACKEND,
            },
            "selected": {
                "best_edge": list(best),
                "worst_edge": list(worst),
                "best_path_3": list(path),
            },
        },
        "validation": [asdict(outcome) for outcome in outcomes],
        "runs": runs,
        "depth_sweep": sweep,
        "qpu_gate": {
            "validated_record_issued": False,
            "qpu_eligible": False,
            "reason": (
                "Smoke circuits only; the QPU-compatible Shor circuit and the gate that issues "
                "validated records are tracked in issue #8."
            ),
        },
        "future_qpu_cost": {
            "price_checked_at": PRICE_CHECKED_AT,
            "source": "https://aws.amazon.com/braket/pricing/",
            "shots": shots,
            "estimated_cost_usd": cost["estimated_cost_usd"],
            "shots_valid": cost["shots_valid"],
        },
        "environment": {
            "amazon_braket_sdk": version("amazon-braket-sdk"),
            "amazon_braket_default_simulator": version("amazon-braket-default-simulator"),
            "amazon_braket_schemas": version("amazon-braket-schemas"),
            "numpy": version("numpy"),
            "matplotlib": version("matplotlib"),
            "networkx": version("networkx"),
        },
    }

    if output_dir is not None:
        stamp = f"{created_at:%Y%m%dT%H%M%S%fZ}"
        run_id = f"emulator-{device_key}-{stamp}-{snapshot.capabilities_sha256[7:19]}"
        artifact_dir = output_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        artifact_path = artifact_dir / "result.json"
        report["artifact_path"] = str(artifact_path)
        if visualize:
            report["visualizations"] = generate_emulation_figures([report], artifact_dir)
        artifact_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return report


def run_emulator_comparison(
    device_keys: list[str],
    *,
    shots: int = 2000,
    sweep_shots: int = 4000,
    output_dir: Path | None = Path("runs/raw"),
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    visualize: bool = True,
) -> dict[str, Any]:
    """Run the per-device report for several devices and render cross-device figures."""
    if not device_keys:
        raise ValueError("at least one device key is required")
    reports = [
        run_emulator_report(
            device_key=key,
            shots=shots,
            sweep_shots=sweep_shots,
            output_dir=output_dir,
            snapshot_dir=snapshot_dir,
            visualize=visualize,
        )
        for key in device_keys
    ]
    created_at = datetime.now(UTC)
    comparison: dict[str, Any] = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "execution": {"class": EXECUTION_CLASS, "aws_requests": False},
        "devices": {
            report["device"]["key"]: {
                "artifact_path": report.get("artifact_path"),
                "calibration_updated_at": report["device"]["snapshot"]["calibration_updated_at"],
                "capabilities_sha256": report["device"]["snapshot"]["capabilities_sha256"],
                "accepted": [row["name"] for row in report["validation"] if row["accepted"]],
                "rejected": [row["name"] for row in report["validation"] if not row["accepted"]],
                "bell_best_edge_tvd": report["runs"]["native-bell-best-edge"]["tvd"],
                "ghz3_tvd": report["runs"]["native-ghz3-best-path"]["tvd"],
            }
            for report in reports
        },
    }
    if output_dir is not None:
        artifact_dir = output_dir / f"emulator-comparison-{created_at:%Y%m%dT%H%M%S%fZ}"
        artifact_dir.mkdir(parents=True, exist_ok=False)
        comparison["artifact_path"] = str(artifact_dir / "comparison.json")
        if visualize:
            comparison["visualizations"] = generate_emulation_figures(reports, artifact_dir)
            (artifact_dir / "report.md").write_text(emulation_markdown(reports), "utf-8")
            comparison["report_markdown"] = "report.md"
        (artifact_dir / "comparison.json").write_text(
            json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    comparison["reports"] = reports
    return comparison
