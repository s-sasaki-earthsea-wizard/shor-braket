# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Tests for the paid submission path, the submission record and the task status report.

Nothing here reaches AWS. ``AwsQuantumTask.create`` is replaced with a stub that records its
arguments, which is the only way to assert what would have been sent without paying for it.
The tests that matter most are the refusals: a plan that did not pass, and a circuit that was
changed after the report was rendered, must not reach the stub at all.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from braket.circuits import Circuit

from shor_braket.aws_session import (
    PROFILE_ENV_AQT,
    PROFILE_ENV_EXEC,
    AwsConfigurationError,
    region,
    results_bucket,
    submission_profile_env,
)
from shor_braket.gate.circuit_hash import circuit_hash
from shor_braket.gate.preflight import Check, preflight
from shor_braket.gate.record import issue_record, save_record
from shor_braket.gate.spending import SpendingLimitStatus
from shor_braket.quantum.native import verbatim
from shor_braket.runner.submit import SubmissionPlan, SubmissionProgram, submit
from shor_braket.runner.task_status import (
    TERMINAL_STATES,
    iter_submissions,
    task_status_report,
)

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
GARNET_ARN = "arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet"


def _native_program() -> Circuit:
    """A small verbatim program on physical qubits Garnet actually has."""
    return verbatim(Circuit().prx(10, 0.5, 0.25).cz(10, 15).prx(15, 1.0, 0.0))


def _passing_plan(tmp_path: Path, garnet_snapshot, *, shots: int = 10) -> SubmissionPlan:
    """Build a plan whose every blocking check passes."""
    from importlib.metadata import version

    program = _native_program()
    save_record(
        issue_record(
            circuit_hash_value=circuit_hash(program),
            device={"key": "garnet", "arn": GARNET_ARN, "name": "IQM Garnet"},
            snapshot={
                "capabilities_sha256": garnet_snapshot.capabilities_sha256,
                "calibration_updated_at": garnet_snapshot.calibration_updated_at,
            },
            problem={"modulus": 15, "base": 7},
            oracle_mode="generic-constant",
            circuit={"canonical_form_version": 1},
            emulation={
                "verdict": {"passed": True, "pass_line": 0.5, "signal_fraction_exact": 0.62}
            },
            environment={"amazon_braket_sdk": version("amazon-braket-sdk")},
            issued_at=NOW,
        ),
        tmp_path,
    )
    report = preflight(
        program,
        device_key="garnet",
        shots=shots,
        snapshot=garnet_snapshot,
        record_dir=tmp_path,
        spending_lookup=lambda arn: SpendingLimitStatus(
            device_arn=arn,
            limit_usd=Decimal("5"),
            current_spend_usd=Decimal("0"),
            queued_spend_usd=Decimal("0"),
        ),
        now=NOW,
    )
    assert report.passed, report.render()
    return SubmissionPlan(
        device_key="garnet",
        oracle_mode="generic-constant",
        shots=shots,
        circuit=program,
        report=report,
        layout=SubmissionProgram(
            circuit=program,
            count_physical=(15, 10),
            work_physical=(20, 18, 14, 19),
            measured=(10, 14, 15, 18, 19, 20),
        ),
    )


class _StubTask:
    """Stands in for the task object AwsQuantumTask.create returns."""

    def __init__(self, arn: str) -> None:
        self.id = arn

    def state(self) -> str:
        return "CREATED"


@pytest.fixture
def created_tasks(monkeypatch):
    """Replace task creation with a recorder, so a test can see what would have been sent."""
    calls: list[dict] = []

    def fake_create(aws_session, **kwargs):
        calls.append(kwargs)
        return _StubTask("arn:aws:braket:eu-north-1:000000000000:quantum-task/stub-0001")

    monkeypatch.setattr("braket.aws.AwsQuantumTask.create", staticmethod(fake_create))
    monkeypatch.setattr("braket.aws.AwsSession", lambda boto_session=None: object())
    return calls


# --- which credentials reach which device ------------------------------------------------


def test_aqt_hardware_needs_the_aqt_profile():
    # Getting this backwards is an AccessDenied rather than a charge, which is the whole point
    # of the role split, but the mapping should still be right the first time (ADR-0004).
    assert submission_profile_env("ibex") == PROFILE_ENV_AQT
    assert submission_profile_env("garnet") == PROFILE_ENV_EXEC
    assert submission_profile_env("emerald") == PROFILE_ENV_EXEC


def test_missing_configuration_is_an_error_not_a_default(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("BRAKET_RESULTS_BUCKET", raising=False)
    with pytest.raises(AwsConfigurationError, match="AWS_REGION"):
        region()
    with pytest.raises(AwsConfigurationError, match="BRAKET_RESULTS_BUCKET"):
        results_bucket()


# --- refusals ----------------------------------------------------------------------------


def test_a_circuit_changed_after_the_report_is_refused(tmp_path, garnet_snapshot, created_tasks):
    plan = _passing_plan(tmp_path, garnet_snapshot)
    # The report was rendered for the program above; swap in a different one afterwards.
    plan.circuit = verbatim(Circuit().prx(10, 0.5, 0.25).cz(10, 15).prx(15, 1.0, 0.5))
    with pytest.raises(ValueError, match="changed after the preflight"):
        submit(plan, session=object(), bucket="amazon-braket-test", run_dir=tmp_path)
    assert created_tasks == []


def test_a_refused_plan_never_reaches_task_creation(tmp_path, garnet_snapshot, created_tasks):
    plan = _passing_plan(tmp_path, garnet_snapshot)
    plan.report.checks.append(
        Check(name="spending limit room", passed=False, detail="0 USD remaining")
    )
    with pytest.raises(PermissionError, match="spending limit room"):
        submit(plan, session=object(), bucket="amazon-braket-test", run_dir=tmp_path)
    assert created_tasks == []


# --- the created task --------------------------------------------------------------------


def test_submission_sends_the_verbatim_circuit_with_its_tags(
    tmp_path, garnet_snapshot, created_tasks
):
    plan = _passing_plan(tmp_path, garnet_snapshot)
    result = submit(
        plan,
        session=object(),
        bucket="amazon-braket-test",
        run_dir=tmp_path,
        caller={"arn": "arn:aws:sts::000000000000:assumed-role/ShorBraketExecutionRole/x"},
        now=NOW,
    )

    assert len(created_tasks) == 1
    sent = created_tasks[0]
    assert sent["device_arn"] == GARNET_ARN
    assert sent["shots"] == 10
    assert sent["s3_destination_folder"] == ("amazon-braket-test", "tasks")
    assert sent["tags"] == {"project": "shor-braket", "oracle": "generic-constant"}
    # Verbatim already pins the qubits; asking for both is rejected by the service.
    assert sent["disable_qubit_rewiring"] is False
    assert circuit_hash(sent["task_specification"]) == plan.report.circuit_hash
    assert result.task_arn.endswith("stub-0001")


def test_the_submission_record_names_the_evidence_behind_the_charge(
    tmp_path, garnet_snapshot, created_tasks
):
    plan = _passing_plan(tmp_path, garnet_snapshot)
    result = submit(
        plan,
        session=object(),
        bucket="amazon-braket-test",
        run_dir=tmp_path,
        caller={"arn": "arn:aws:sts::000000000000:assumed-role/ShorBraketExecutionRole/x"},
        now=NOW,
    )

    record = json.loads(result.record_path.read_text(encoding="utf-8"))
    assert record["task"]["arn"] == result.task_arn
    assert record["circuit_hash"] == plan.report.circuit_hash
    assert record["validated_record"]["circuit_hash"] == plan.report.circuit_hash
    assert record["validated_record"]["capabilities_sha256"] == (
        garnet_snapshot.capabilities_sha256
    )
    assert record["cost"]["estimated_cost_usd"] == "0.31450"
    assert record["spending_limit_at_submission"]["limit_usd"] == "5"
    assert record["tags"]["oracle"] == "generic-constant"
    assert record["caller"]["arn"].endswith("/x")
    assert record["preflight"]["passed"] is True


# --- task status -------------------------------------------------------------------------


class _StubBraketClient:
    """Answers GetQuantumTask from a fixed table."""

    def __init__(self, table):
        self.table = table
        self.asked: list[str] = []

    def get_quantum_task(self, *, quantumTaskArn):  # noqa: N803 - the API spells it this way
        self.asked.append(quantumTaskArn)
        return self.table[quantumTaskArn]


class _StubSession:
    def __init__(self, client):
        self._client = client

    def client(self, name):
        assert name == "braket"
        return self._client


def test_status_reads_every_task_the_repository_recorded(
    tmp_path, garnet_snapshot, created_tasks
):
    plan = _passing_plan(tmp_path, garnet_snapshot)
    result = submit(
        plan, session=object(), bucket="amazon-braket-test", run_dir=tmp_path, now=NOW
    )

    assert [task.task_arn for task in iter_submissions(tmp_path)] == [result.task_arn]

    client = _StubBraketClient(
        {
            result.task_arn: {
                "status": "COMPLETED",
                "deviceArn": GARNET_ARN,
                "shots": 10,
                "createdAt": datetime(2026, 9, 21, 9, 1, tzinfo=UTC),
                "endedAt": datetime(2026, 9, 21, 9, 4, tzinfo=UTC),
                "outputS3Bucket": "amazon-braket-test",
                "outputS3Directory": "tasks/stub-0001",
            }
        }
    )
    report = task_status_report(_StubSession(client), run_dir=tmp_path)

    assert report["count"] == 1
    entry = report["tasks"][0]
    assert entry["status"] == "COMPLETED"
    assert entry["terminal"] is True
    assert entry["created_at"] == "2026-09-21T09:01:00+00:00"
    assert entry["results"]["directory"] == "tasks/stub-0001"
    # The ledger fields come from the record, not from the service.
    assert entry["oracle_mode"] == "generic-constant"
    assert entry["estimated_cost_usd"] == "0.31450"


def test_a_queued_task_is_not_reported_as_finished(tmp_path):
    client = _StubBraketClient({"arn:task": {"status": "QUEUED", "deviceArn": GARNET_ARN}})
    report = task_status_report(_StubSession(client), task_arn="arn:task", run_dir=tmp_path)
    assert report["tasks"][0]["terminal"] is False
    assert "QUEUED" not in TERMINAL_STATES


def test_an_unreadable_record_does_not_hide_the_rest(tmp_path):
    (tmp_path / "qpu-broken").mkdir()
    (tmp_path / "qpu-broken" / "submission.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "qpu-ok").mkdir()
    (tmp_path / "qpu-ok" / "submission.json").write_text(
        json.dumps({"task": {"arn": "arn:task"}, "submitted_at": "2026-09-21T09:00:00+00:00"}),
        encoding="utf-8",
    )
    assert [task.task_arn for task in iter_submissions(tmp_path)] == ["arn:task"]


# --- the dry run ---------------------------------------------------------------------------


def test_a_dry_run_reports_the_offline_checks_and_does_not_fail_on_the_unread_limit(tmp_path):
    """Without credentials the spending limit cannot be read, and that alone is not a failure."""
    from typer.testing import CliRunner

    from shor_braket.cli import app

    runner = CliRunner()
    # An empty record directory is an offline failure, and must still be reported as one.
    result = runner.invoke(
        app,
        [
            "submit-qpu",
            "--device",
            "garnet",
            "--shots",
            "10",
            "--oracle",
            "generic-constant",
            "--record-dir",
            str(tmp_path),
            "--no-execute",
        ],
    )
    assert result.exit_code == 1
    assert "offline checks failed" in result.output
    assert "validated record" in result.output


def test_a_dry_run_never_builds_a_session_or_a_bucket(monkeypatch, tmp_path):
    """--no-execute must not touch AWS at all, not even to find out who it would be."""
    from typer.testing import CliRunner

    from shor_braket.cli import app

    def explode(*args, **kwargs):
        raise AssertionError("a dry run must not reach AWS")

    monkeypatch.setattr("shor_braket.cli.submission_session", explode)
    monkeypatch.setattr("shor_braket.cli.caller_identity", explode)
    monkeypatch.setattr("shor_braket.cli.results_bucket", explode)

    result = CliRunner().invoke(
        app,
        [
            "submit-qpu",
            "--device",
            "garnet",
            "--shots",
            "10",
            "--record-dir",
            str(tmp_path),
            "--no-execute",
        ],
    )
    assert result.exit_code == 1
    assert "offline checks failed" in result.output


# --- the register layout ------------------------------------------------------------------


def test_the_recorded_layout_is_the_one_the_emulation_used(snapshot_dir, garnet_snapshot):
    """A result is a bit per physical qubit; without this mapping it cannot be read back."""
    from shor_braket.devices import summarize_snapshot
    from shor_braket.runner.n15 import emulate_n15_configuration
    from shor_braket.runner.submit import build_submission_program

    emulated = emulate_n15_configuration(
        garnet_snapshot,
        summarize_snapshot(garnet_snapshot),
        oracle_mode="generic-constant",
        shots=1,
        max_permutations=1,
    )
    program = build_submission_program(
        device_key="garnet",
        oracle_mode="generic-constant",
        snapshot_dir=snapshot_dir,
        max_permutations=1,
    )

    # The emulation labels each physical qubit c0/c1 (count, value order) and w3..w0 (work).
    roles = emulated["layout"]["roles"]
    expected_count = [
        qubit for qubit, role in sorted(roles.items(), key=lambda kv: kv[1]) if role.startswith("c")
    ]
    assert [str(q) for q in program.count_physical] == expected_count
    assert sorted(program.measured) == sorted(int(q) for q in roles)
    assert len(program.work_physical) == 4


def test_the_submission_record_carries_the_layout(tmp_path, garnet_snapshot, created_tasks):
    plan = _passing_plan(tmp_path, garnet_snapshot)
    result = submit(
        plan, session=object(), bucket="amazon-braket-test", run_dir=tmp_path, now=NOW
    )
    record = json.loads(result.record_path.read_text(encoding="utf-8"))
    assert record["register_layout"]["count_physical"] == [15, 10]
    assert record["register_layout"]["work_physical"] == [20, 18, 14, 19]
    assert record["register_layout"]["measured"] == [10, 14, 15, 18, 19, 20]
    assert record["problem"] == {"modulus": 15, "base": 7, "count_qubit_count": 2}
