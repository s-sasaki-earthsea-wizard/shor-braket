# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Tests for the hardware result analysis.

Nothing here touches AWS: every result document is synthesised on disk and fed through the
``--result-file`` path. The two anchors are a device that returns the ideal distribution, which
must read as a signal fraction of one, and a device that returns uniform noise, which must read
as zero however many factors of 15 fall out of it along the way.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from shor_braket.analysis.distribution import expected_joint_probabilities
from shor_braket.runner.report import (
    analyze_submission,
    parse_task_result,
    run_report,
)

COUNT_PHYSICAL = [15, 10]
WORK_PHYSICAL = [20, 18, 14, 19]
MEASURED = [10, 14, 15, 18, 19, 20]
DESIRED = [*COUNT_PHYSICAL, *WORK_PHYSICAL]
TASK_ARN = "arn:aws:braket:eu-north-1:000000000000:quantum-task/stub-0001"


def _shot_for(index: int) -> list[int]:
    """Build one measurement row whose desired-order reading is ``index``.

    The device reports one bit per measured qubit in ascending physical order, so the bits of
    ``index`` (most significant first, in register order) have to be scattered to the positions
    those qubits occupy in that ascending list. Getting this backwards is exactly the mistake
    the recorded layout exists to prevent, so the test does the scattering the long way round.
    """
    bits = format(index, f"0{len(DESIRED)}b")
    row = [0] * len(MEASURED)
    for bit, qubit in zip(bits, DESIRED, strict=True):
        row[MEASURED.index(qubit)] = int(bit)
    return row


def _result_document(indices) -> dict:
    """A Braket gate-model result document over the measured qubits."""
    return {
        "braketSchemaHeader": {"name": "braket.task_result.gate_model_task_result"},
        "measuredQubits": MEASURED,
        "measurements": [_shot_for(int(index)) for index in indices],
    }


def _ideal_indices(shots: int, seed: int = 0):
    """Draw shots from the ideal joint distribution."""
    expected = expected_joint_probabilities(
        modulus=15, base=7, count_qubit_count=2, work_qubit_count=4
    )
    rng = np.random.default_rng(seed)
    return rng.choice(expected.size, size=shots, p=expected)


def _submission_record(directory: Path, *, predicted: float = 0.521) -> Path:
    """Write a submission record of the shape make submit-qpu leaves behind."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "submitted_at": "2026-09-21T09:00:00+00:00",
        "task": {"arn": TASK_ARN, "status_at_creation": "CREATED"},
        "device": {
            "key": "garnet",
            "arn": "arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet",
            "name": "IQM Garnet",
        },
        "oracle_mode": "generic-constant",
        "shots": 2000,
        "circuit_hash": "sha256:" + "0" * 64,
        "register_layout": {
            "count_physical": COUNT_PHYSICAL,
            "work_physical": WORK_PHYSICAL,
            "measured": MEASURED,
        },
        "problem": {"modulus": 15, "base": 7, "count_qubit_count": 2},
        "validated_record": {"issued_at": "2026-09-18T17:02:58+00:00"},
        "cost": {"estimated_cost_usd": "3.20000"},
        "tags": {"project": "shor-braket", "oracle": "generic-constant"},
        "preflight": {
            "checks": [
                {
                    "name": "emulation verdict",
                    "passed": True,
                    "detail": f"signal fraction {predicted:.3f} >= 0.5",
                }
            ]
        },
    }
    path = directory / "submission.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# --- reading the result document ----------------------------------------------------------


def test_a_result_without_measurements_is_refused():
    with pytest.raises(ValueError, match="shot-based"):
        parse_task_result({"resultTypes": [], "measuredQubits": MEASURED})


def test_counts_are_keyed_by_the_measured_qubit_order():
    parsed = parse_task_result(_result_document([0, 0, 1]))
    assert parsed.shots == 3
    assert sum(parsed.counts.values()) == 3
    assert all(len(state) == len(MEASURED) for state in parsed.counts)


# --- the two anchors ----------------------------------------------------------------------


def test_an_ideal_device_reads_as_a_signal_fraction_of_one(tmp_path):
    directory = tmp_path / "qpu-garnet-generic-constant-x-abc"
    _submission_record(directory)
    result_file = tmp_path / "results.json"
    result_file.write_text(json.dumps(_result_document(_ideal_indices(20000))), encoding="utf-8")

    report = run_report(None, result_file=result_file, run_dir=tmp_path)

    assert report["count"] == 1
    entry = report["tasks"][0]
    assert entry["analyzed"] is True
    assert entry["passed"] is True
    assert entry["signal_fraction"] == pytest.approx(1.0, abs=0.02)


def test_a_uniform_device_reads_as_a_signal_fraction_of_zero(tmp_path):
    directory = tmp_path / "qpu-garnet-generic-constant-x-abc"
    _submission_record(directory)
    rng = np.random.default_rng(1)
    result_file = tmp_path / "results.json"
    result_file.write_text(
        json.dumps(_result_document(rng.integers(0, 64, size=20000))), encoding="utf-8"
    )

    report = run_report(None, result_file=result_file, run_dir=tmp_path)
    entry = report["tasks"][0]

    assert entry["signal_fraction"] == pytest.approx(0.0, abs=0.03)
    assert entry["passed"] is False

    # The order recovery rate is near its own baseline, which is the point of reporting both:
    # a device that knows nothing still "recovers" the order most of the time at t = 2.
    analysis = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
    metrics = analysis["metrics"]
    assert metrics["order_recovery_rate_sampled"] == pytest.approx(
        metrics["order_recovery_baseline_uniform_y"], abs=0.05
    )


# --- what the report says -----------------------------------------------------------------


def test_the_report_compares_the_measurement_with_the_emulated_prediction(tmp_path):
    directory = tmp_path / "qpu-garnet-generic-constant-x-abc"
    _submission_record(directory, predicted=0.521)
    result_file = tmp_path / "results.json"
    result_file.write_text(json.dumps(_result_document(_ideal_indices(20000))), encoding="utf-8")

    run_report(None, result_file=result_file, run_dir=tmp_path)
    analysis = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))

    assert analysis["metrics"]["predicted_signal_fraction"] == 0.521
    difference = analysis["metrics"]["signal_fraction_difference"]
    assert difference == pytest.approx(
        analysis["metrics"]["signal_fraction_support_mass"] - 0.521, abs=1e-12
    )
    assert "not a factoring claim" in analysis["claim"]

    markdown = (directory / "report.md").read_text(encoding="utf-8")
    assert "エミュレーションの予測 λ" in markdown
    assert "合否には使わない" in markdown
    assert TASK_ARN in markdown


def test_a_record_without_a_layout_cannot_be_analysed(tmp_path):
    directory = tmp_path / "qpu-old"
    path = _submission_record(directory)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["register_layout"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="register_layout"):
        analyze_submission(payload, _result_document([0]))


def test_reporting_with_nothing_submitted_says_so(tmp_path):
    with pytest.raises(ValueError, match="nothing has been submitted yet"):
        run_report(None, result_file=tmp_path / "missing.json", run_dir=tmp_path)
