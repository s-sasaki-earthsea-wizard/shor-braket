# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Turn a finished quantum task into the one number this project is after.

The question is not "did it factor 15". A t = 2 circuit built from the N = 15 specific swap
network already knows the answer, and even a device returning uniform noise yields 3 x 5 about
12% of the time (ADR-0001). The question is how much of the period-4 signal survived the
hardware, which is the same lambda the emulator predicted, measured on the real machine.

So the headline of a report is a comparison: the lambda the emulation predicted for this exact
circuit against the lambda the device delivered. The validated record holds the prediction,
the submission record holds the layout, and the task result holds the counts.

Two estimators of lambda are reported and they are not interchangeable:

* the **support-mass** estimator is linear in the counts, so its sampled value is unbiased at
  any number of shots. This is the one the verdict uses;
* the **TVD** estimator is biased upward at small shot counts, because a finite sample sits a
  sampling floor away from its own source even when the device is perfect. At the 10 shots of a
  route check that bias dominates, so the number is shown next to the floor and not judged.

Nothing here creates work or costs money: results are read from S3 with the read-only profile,
or from a file that was downloaded earlier.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from shor_braket.analysis.distribution import (
    expected_joint_probabilities,
    hellinger_fidelity,
    low_bit_visibility,
    noisy_verdict,
    orbit_mass,
    sampling_floor,
    signal_fraction,
    support_signal_fraction,
    support_signal_fraction_error,
    total_variation_distance,
)
from shor_braket.classical.order import multiplicative_order
from shor_braket.quantum.n15 import MODULUS
from shor_braket.runner.n15 import joint_from_counts, order_recovery
from shor_braket.runner.submit import DEFAULT_RUN_DIR
from shor_braket.runner.task_status import SubmittedTask, describe_task, iter_submissions
from shor_braket.visualization.qpu import qpu_markdown

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    import boto3

EXECUTION_CLASS = "qpu"
RESULTS_FILE = "results.json"
PROBABILITY_TOLERANCE = 1e-9


@dataclass(frozen=True)
class TaskResult:
    """The measurement outcomes of one finished task."""

    counts: dict[str, int]
    measured_qubits: list[int]
    shots: int


def parse_task_result(payload: dict[str, Any]) -> TaskResult:
    """Read Braket's ``results.json`` into counts keyed by bit string.

    Args:
        payload: The parsed result document the service wrote to S3.

    Returns:
        The counts, the qubits they are indexed by, and the shot total.

    Raises:
        ValueError: If the document has no per-shot measurements. Only shot-based results are
            handled here; a result type document would mean the circuit was built differently
            from the one this project submits.
    """
    measurements = payload.get("measurements")
    measured_qubits = payload.get("measuredQubits")
    if not measurements or not measured_qubits:
        raise ValueError(
            "the result document has no measurements/measuredQubits; this reader only "
            "understands shot-based gate model results"
        )
    counter: Counter[str] = Counter()
    for shot in measurements:
        counter["".join(str(int(bit)) for bit in shot)] += 1
    return TaskResult(
        counts=dict(sorted(counter.items())),
        measured_qubits=[int(qubit) for qubit in measured_qubits],
        shots=sum(counter.values()),
    )


def fetch_task_result(session: boto3.Session, *, bucket: str, directory: str) -> dict[str, Any]:
    """Download one task's result document from the results bucket.

    Args:
        session: A read-only boto3 session.
        bucket: Results bucket.
        directory: The task's directory inside the bucket, as ``GetQuantumTask`` reports it.

    Returns:
        The parsed result document.
    """
    key = f"{directory.rstrip('/')}/{RESULTS_FILE}"
    body = session.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def analyze_counts(
    result: TaskResult,
    *,
    count_physical: list[int],
    work_physical: list[int],
    base: int,
    predicted_signal_fraction: float | None = None,
) -> dict[str, Any]:
    """Compare a device's counts with the ideal joint distribution.

    Args:
        result: The task's measurement outcomes.
        count_physical: Physical qubits of the count register, most significant first.
        work_physical: Physical qubits of the work register, most significant first.
        base: The base whose order was being found.
        predicted_signal_fraction: The emulator's lambda for this circuit, for the comparison
            that is the point of the experiment. Omitted when no record is available.

    Returns:
        The metrics, the verdict and both distributions.
    """
    desired = [*count_physical, *work_physical]
    count_qubit_count = len(count_physical)
    sampled = joint_from_counts(result.counts, result.measured_qubits, desired)
    expected = expected_joint_probabilities(
        modulus=MODULUS,
        base=base,
        count_qubit_count=count_qubit_count,
        work_qubit_count=len(work_physical),
    )
    uniform: NDArray[np.float64] = np.full_like(expected, 1.0 / expected.size)

    support = expected > PROBABILITY_TOLERANCE
    support_fraction = float(support.mean())
    support_mass = float(sampled[support].sum())
    tvd_ideal_uniform = total_variation_distance(expected, uniform)
    sampled_tvd = total_variation_distance(sampled, expected)

    lambda_support = support_signal_fraction(support_mass, support_fraction)
    lambda_error = support_signal_fraction_error(support_mass, support_fraction, result.shots)
    lambda_tvd = signal_fraction(sampled_tvd, tvd_ideal_uniform)

    recovery = order_recovery(
        result.counts,
        result.measured_qubits,
        count_physical,
        base=base,
        count_qubit_count=count_qubit_count,
    )

    # The verdict's pass line wants the best available estimate of lambda. On hardware there is
    # no exact distribution to take it from, so the unbiased support-mass estimator stands in
    # for the emulator's exact one; the TVD estimator would fail small runs on sampling bias
    # alone. The detection test is unchanged (issue #7).
    verdict = noisy_verdict(
        signal_fraction_exact=lambda_support,
        support_mass_sampled=support_mass,
        support_fraction=support_fraction,
        shots=result.shots,
    )

    return {
        "shots": result.shots,
        "register_order_physical": desired,
        "metrics": {
            "sampled_tvd": sampled_tvd,
            "tvd_ideal_vs_uniform": tvd_ideal_uniform,
            "sampling_floor_estimate": sampling_floor(expected, result.shots),
            "signal_fraction_support_mass": lambda_support,
            "signal_fraction_support_mass_error": lambda_error,
            "signal_fraction_tvd": lambda_tvd,
            "ideal_support_mass_sampled": support_mass,
            "support_fraction": support_fraction,
            # What the multiplication network is responsible for. At t = 2 the support-mass
            # signal fraction is this number rescaled and says nothing about the count register.
            "orbit_mass": orbit_mass(
                sampled, modulus=MODULUS, base=base, work_qubit_count=len(work_physical)
            ),
            # Coherence of the count qubits that control U^(2^k) = I, from t = 3 up.
            "low_bit_visibility": low_bit_visibility(
                sampled,
                count_qubit_count=count_qubit_count,
                work_qubit_count=len(work_physical),
                order=multiplicative_order(base, MODULUS),
            ),
            "hellinger_fidelity": hellinger_fidelity(sampled, expected),
            "order_recovery_rate_sampled": recovery["rate"],
            "order_recovery_baseline_uniform_y": recovery["baseline_uniform_y"],
            "predicted_signal_fraction": predicted_signal_fraction,
            "signal_fraction_difference": (
                None
                if predicted_signal_fraction is None
                else lambda_support - predicted_signal_fraction
            ),
            "verdict": verdict,
        },
        "distributions": {"expected": expected.tolist(), "sampled": sampled.tolist()},
        "measurement_counts": result.counts,
        "measured_qubits": result.measured_qubits,
    }


def _submission_payload(task: SubmittedTask) -> dict[str, Any]:
    """Read the full submission record behind one task."""
    payload: dict[str, Any] = json.loads(task.record_path.read_text(encoding="utf-8"))
    return payload


def analyze_submission(
    submission: dict[str, Any],
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    """Analyze one task given its submission record and its result document.

    Args:
        submission: The submission record written when the task was created.
        result_payload: The parsed ``results.json`` of the same task.

    Returns:
        The analysis, including the identity of the circuit it belongs to.

    Raises:
        ValueError: If the submission record has no register layout, which means the result
            cannot be mapped onto the count and work registers.
    """
    layout = submission.get("register_layout")
    if not layout:
        raise ValueError(
            "the submission record has no register_layout, so the measured bits cannot be "
            "assigned to the count and work registers; this task predates that field"
        )
    problem = submission.get("problem") or {}
    record = submission.get("validated_record") or {}
    predicted = None
    verdict = (submission.get("preflight") or {}).get("checks") or []
    for check in verdict:
        if check.get("name") == "emulation verdict" and check.get("passed"):
            predicted = _parse_predicted(str(check.get("detail", "")))
            break

    analysis = analyze_counts(
        parse_task_result(result_payload),
        count_physical=[int(q) for q in layout["count_physical"]],
        work_physical=[int(q) for q in layout["work_physical"]],
        base=int(problem.get("base", 7)),
        predicted_signal_fraction=predicted,
    )
    analysis.update(
        {
            "execution": {"class": EXECUTION_CLASS, "analyzed_at": datetime.now(UTC).isoformat()},
            "task": submission.get("task"),
            "device": submission.get("device"),
            "oracle_mode": submission.get("oracle_mode"),
            "circuit_hash": submission.get("circuit_hash"),
            "validated_record": record,
            "cost": submission.get("cost"),
            "claim": (
                "Observation of period-4 signal survival on hardware under the N = 15 specific "
                "decomposition with t = 2; not a factoring claim."
            ),
        }
    )
    return analysis


def _parse_predicted(detail: str) -> float | None:
    """Pull the emulated signal fraction out of the preflight check's wording.

    The preflight renders it as ``signal fraction 0.521 >= 0.5``. Reading it back from there
    keeps the comparison available even when the validated record has been re-issued since.
    """
    parts = detail.split()
    for index, word in enumerate(parts):
        if word == "fraction" and index + 1 < len(parts):
            try:
                return float(parts[index + 1])
            except ValueError:
                return None
    return None


def run_report(
    session: boto3.Session | None,
    *,
    task_arn: str | None = None,
    result_file: Path | None = None,
    run_dir: Path = DEFAULT_RUN_DIR,
) -> dict[str, Any]:
    """Analyze every finished task this repository recorded, or one named task.

    Args:
        session: A read-only boto3 session, or ``None`` when ``result_file`` supplies the data.
        task_arn: Restrict the report to one task.
        result_file: Read the result from this file instead of S3, for offline work.
        run_dir: Directory holding the raw run artifacts.

    Returns:
        A summary naming every task considered and where its analysis was written.

    Raises:
        ValueError: If no submission record matches, or if a result is needed from S3 without a
            session.
    """
    submissions = [
        task
        for task in iter_submissions(run_dir)
        if task_arn is None or task.task_arn == task_arn
    ]
    if not submissions:
        raise ValueError(
            f"no submission record found in {run_dir}"
            + (f" for {task_arn}" if task_arn else "; nothing has been submitted yet")
        )

    analyzed: list[dict[str, Any]] = []
    for task in submissions:
        submission = _submission_payload(task)
        status: dict[str, Any] = {}
        if result_file is not None:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
        else:
            if session is None:
                raise ValueError("reading a result from S3 needs a session")
            status = describe_task(session, task.task_arn)
            if status["status"] != "COMPLETED":
                analyzed.append(
                    {
                        "task_arn": task.task_arn,
                        "status": status["status"],
                        "analyzed": False,
                        "reason": "the task has not completed; nothing to analyze yet",
                    }
                )
                continue
            results = status.get("results") or {}
            payload = fetch_task_result(
                session, bucket=results["bucket"], directory=results["directory"]
            )

        analysis = analyze_submission(submission, payload)
        analysis["task_status"] = status
        directory = task.record_path.parent
        (directory / "analysis.json").write_text(
            json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (directory / "report.md").write_text(qpu_markdown(analysis), encoding="utf-8")
        analyzed.append(
            {
                "task_arn": task.task_arn,
                "status": status.get("status", "from file"),
                "analyzed": True,
                "device": analysis["device"],
                "oracle_mode": analysis["oracle_mode"],
                "signal_fraction": analysis["metrics"]["signal_fraction_support_mass"],
                "passed": analysis["metrics"]["verdict"]["passed"],
                "report": str(directory / "report.md"),
            }
        )
    return {"run_dir": str(run_dir), "count": len(analyzed), "tasks": analyzed}
