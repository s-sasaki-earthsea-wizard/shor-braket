# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Assemble a QPU submission, run it through the preflight, and create the task.

This module builds the same program the emulation built, runs it through
:func:`~shor_braket.gate.preflight.preflight`, and only then calls ``AwsQuantumTask.create``.
There is deliberately no other path to a paid task in this package: anything that wants one
goes through :func:`plan_submission` and then :func:`submit`, and :func:`submit` refuses a plan
whose preflight did not pass.

Keeping the assembly here, separate from the emulation runner, is what makes the hash check
meaningful. The program is rebuilt from the same inputs, so if anything about the circuit, the
routing or the lowering changed since the record was issued, the rebuilt hash simply will not
match the record and the preflight refuses.

Every created task writes a submission record next to the raw results. That record is the answer
to "what did we pay for, on what evidence": it names the task, the circuit hash, the validated
record behind it, the tags, the estimate and the spending limit as it stood at that moment. It
lives under ``runs/raw/``, which is gitignored, so it may hold the account and the principal ARN
that a tracked file must not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from braket.circuits import Circuit

from shor_braket.cost import QPU_CANDIDATES
from shor_braket.devices.calibration import summarize_snapshot
from shor_braket.devices.snapshot import DEFAULT_SNAPSHOT_DIR, load_snapshot
from shor_braket.gate.circuit_hash import circuit_hash, hash_digest
from shor_braket.gate.preflight import PreflightReport, preflight
from shor_braket.gate.record import DEFAULT_RECORD_DIR
from shor_braket.gate.spending import SpendingLimitLookup, no_spending_limit_lookup
from shor_braket.quantum.compile import compile_to_native
from shor_braket.quantum.n15 import ORACLE_MODES, build_n15_circuit
from shor_braket.quantum.native import gate_family, verbatim
from shor_braket.quantum.routing import choose_layout

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    import boto3

DEFAULT_RUN_DIR = Path("runs/raw")
SUBMISSION_SCHEMA_VERSION = 1

# Prefix inside the results bucket. Braket appends the task id, so one prefix for the project
# keeps every result under one place the lifecycle rules and the readonly policy already cover.
DEFAULT_RESULTS_PREFIX = "tasks"

SUBMISSION_REFUSED_REASON = (
    "The preflight did not pass, so no task was created. Inspect the report's blocking checks; "
    "see docs/03-execution-gate.md."
)


@dataclass
class SubmissionPlan:
    """A program, the device it targets, and the preflight's verdict on the pair."""

    device_key: str
    oracle_mode: str
    shots: int
    circuit: Circuit
    report: PreflightReport

    @property
    def allowed(self) -> bool:
        """Report whether the preflight cleared this submission."""
        return self.report.passed

    def to_dict(self) -> dict[str, Any]:
        """Serialize the plan without the circuit object."""
        return {
            "device": self.device_key,
            "oracle_mode": self.oracle_mode,
            "shots": self.shots,
            "preflight": self.report.to_dict(),
            "submission": {
                "attempted": False,
                "reason": None if self.allowed else SUBMISSION_REFUSED_REASON,
            },
        }


@dataclass
class SubmissionResult:
    """A created quantum task and the evidence trail that let it be created."""

    task_arn: str
    task_status: str
    submitted_at: str
    plan: SubmissionPlan
    caller: dict[str, str]
    results_location: dict[str, str]
    record_path: Path

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result for JSON."""
        return {
            "task": {"arn": self.task_arn, "status_at_creation": self.task_status},
            "submitted_at": self.submitted_at,
            "device": self.plan.device_key,
            "oracle_mode": self.plan.oracle_mode,
            "shots": self.plan.shots,
            "circuit_hash": self.plan.report.circuit_hash,
            "results": self.results_location,
            "submission_record": str(self.record_path),
        }


def build_submission_circuit(
    *,
    device_key: str,
    oracle_mode: str,
    count_qubit_count: int = 2,
    base: int = 7,
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    max_permutations: int | None = None,
) -> Circuit:
    """Rebuild the verbatim program for one device and oracle.

    The steps mirror :func:`~shor_braket.runner.n15.emulate_n15_configuration` exactly, because a
    submission that is routed or lowered differently from the emulation is a different program
    and must fail the hash check.

    Args:
        device_key: Logical device name.
        oracle_mode: Which oracle to build.
        count_qubit_count: Size of the count register.
        base: The base whose order is being found.
        snapshot_dir: Where committed calibration snapshots live.
        max_permutations: Cap on the layout search, for fast tests.

    Returns:
        The verbatim circuit, ready to hash.

    Raises:
        ValueError: If the device key or oracle mode is not an approved one.
    """
    if device_key not in QPU_CANDIDATES:
        raise ValueError(
            f"unknown device key {device_key!r}; approved devices are {sorted(QPU_CANDIDATES)}"
        )
    if oracle_mode not in ORACLE_MODES:
        raise ValueError(f"unknown oracle mode {oracle_mode!r}; expected {list(ORACLE_MODES)}")

    snapshot = load_snapshot(device_key, snapshot_dir)
    summary = summarize_snapshot(snapshot)
    logical = build_n15_circuit(
        base=base, count_qubit_count=count_qubit_count, oracle_mode=oracle_mode
    )
    logical_qubits = sorted([*logical.count_qubits_physical, *logical.work_qubits])
    routed = choose_layout(
        logical.circuit, summary, logical_qubits, max_permutations=max_permutations
    )
    native = compile_to_native(routed.circuit, gate_family(summary.native_gate_set))
    return verbatim(native.circuit)


def plan_submission(
    *,
    device_key: str,
    oracle_mode: str,
    shots: int,
    count_qubit_count: int = 2,
    base: int = 7,
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    record_dir: Path = DEFAULT_RECORD_DIR,
    max_cost_usd: Decimal | None = None,
    spending_lookup: SpendingLimitLookup = no_spending_limit_lookup,
    now: datetime | None = None,
    max_permutations: int | None = None,
    campaign: str | None = None,
) -> SubmissionPlan:
    """Rebuild the program and run the full preflight against it.

    Args:
        device_key: Logical device name; the ARN is resolved from the approved candidate list.
        oracle_mode: Which oracle to build.
        shots: Shots the task would request.
        count_qubit_count: Size of the count register.
        base: The base whose order is being found.
        snapshot_dir: Where committed calibration snapshots live.
        record_dir: Where validated records live.
        max_cost_usd: Per-task ceiling; defaults to ``BRAKET_MAX_COST_USD`` or 10 USD.
        spending_lookup: How to read the service-side spending limit.
        now: Point in time for the freshness checks.
        max_permutations: Cap on the layout search, for fast tests.
        campaign: Optional campaign tag for cost grouping; it grants no access.

    Returns:
        The plan, whose :attr:`SubmissionPlan.allowed` says whether every blocking check passed.
    """
    circuit = build_submission_circuit(
        device_key=device_key,
        oracle_mode=oracle_mode,
        count_qubit_count=count_qubit_count,
        base=base,
        snapshot_dir=snapshot_dir,
        max_permutations=max_permutations,
    )
    report = preflight(
        circuit,
        device_key=device_key,
        shots=shots,
        snapshot=load_snapshot(device_key, snapshot_dir),
        record_dir=record_dir,
        max_cost_usd=max_cost_usd,
        spending_lookup=spending_lookup,
        now=now or datetime.now(UTC),
        campaign=campaign,
    )
    return SubmissionPlan(
        device_key=device_key,
        oracle_mode=oracle_mode,
        shots=shots,
        circuit=circuit,
        report=report,
    )


def _submission_dir(plan: SubmissionPlan, moment: datetime, run_dir: Path) -> Path:
    """Build the directory one submission's artifacts live in.

    Args:
        plan: The plan being submitted.
        moment: Submission time, used in the directory name.
        run_dir: Parent directory for raw run artifacts.

    Returns:
        The directory path, not yet created.
    """
    stamp = moment.strftime("%Y%m%dT%H%M%S%fZ")
    digest = hash_digest(plan.report.circuit_hash)[:12]
    return run_dir / f"qpu-{plan.device_key}-{plan.oracle_mode}-{stamp}-{digest}"


def _submission_record(
    *,
    plan: SubmissionPlan,
    task_arn: str,
    task_status: str,
    moment: datetime,
    caller: dict[str, str],
    results_location: dict[str, str],
) -> dict[str, Any]:
    """Build the audit record for one created task.

    Args:
        plan: The plan that was submitted.
        task_arn: ARN of the created task.
        task_status: Status the service reported at creation.
        moment: Submission time.
        caller: STS identity that created the task.
        results_location: Bucket and prefix the results will be written to.

    Returns:
        The record, ready to serialize.
    """
    record = plan.report.record
    return {
        "schema_version": SUBMISSION_SCHEMA_VERSION,
        "submitted_at": moment.isoformat(),
        "task": {"arn": task_arn, "status_at_creation": task_status},
        "device": {
            "key": plan.device_key,
            "arn": plan.report.device_arn,
            "name": plan.report.device_name,
        },
        "oracle_mode": plan.oracle_mode,
        "shots": plan.shots,
        "circuit_hash": plan.report.circuit_hash,
        "validated_record": {
            "circuit_hash": record.circuit_hash,
            "issued_at": record.issued_at,
            "expires_at": record.expires_at,
            "git_commit": record.git_commit,
            "calibration_updated_at": record.snapshot.get("calibration_updated_at"),
            "capabilities_sha256": record.snapshot.get("capabilities_sha256"),
        }
        if record is not None
        else None,
        "tags": plan.report.tags,
        "cost": plan.report.cost,
        "spending_limit_at_submission": plan.report.spending_limit,
        # runs/raw is gitignored, which is why naming the principal here is allowed.
        "caller": caller,
        "results": results_location,
        "environment": {
            "amazon_braket_sdk": version("amazon-braket-sdk"),
            "boto3": version("boto3"),
        },
        "preflight": plan.report.to_dict(),
    }


def submit(
    plan: SubmissionPlan,
    *,
    session: boto3.Session,
    bucket: str,
    prefix: str = DEFAULT_RESULTS_PREFIX,
    run_dir: Path = DEFAULT_RUN_DIR,
    caller: dict[str, str] | None = None,
    now: datetime | None = None,
) -> SubmissionResult:
    """Create the quantum task this plan describes, and record what was paid for.

    The plan is checked twice on the way in. Its preflight verdict must still be a pass, and the
    circuit is re-hashed to confirm it is the program the preflight approved: a plan is a mutable
    object, and the only claim worth anything is one made about the bytes actually sent.

    Args:
        plan: A plan whose preflight passed.
        session: A boto3 session for the role allowed to submit to this device. Build it with
            :func:`~shor_braket.aws_session.submission_session` so the IQM/AQT split is honoured.
        bucket: Results bucket. Braket's service-linked role can only write to a bucket whose
            name starts with ``amazon-braket-``.
        prefix: Key prefix inside the bucket.
        run_dir: Where to write the submission record.
        caller: STS identity of the session, if it has already been read. Read when omitted.
        now: Submission time, for tests.

    Returns:
        The created task and the path of its submission record.

    Raises:
        PermissionError: If the plan's preflight did not pass.
        ValueError: If the circuit no longer hashes to what the preflight approved.
    """
    if not plan.allowed:
        blockers = ", ".join(check.name for check in plan.report.blockers)
        raise PermissionError(f"{SUBMISSION_REFUSED_REASON} Blocking checks: {blockers}.")

    rebuilt = circuit_hash(plan.circuit)
    if rebuilt != plan.report.circuit_hash:
        raise ValueError(
            "the circuit changed after the preflight ran: the report approved "
            f"{plan.report.circuit_hash} but the circuit now hashes to {rebuilt}"
        )

    from braket.aws import AwsQuantumTask, AwsSession

    moment = now or datetime.now(UTC)
    aws_session = AwsSession(boto_session=session)
    identity = caller if caller is not None else {}

    task = AwsQuantumTask.create(
        aws_session,
        device_arn=plan.report.device_arn,
        task_specification=plan.circuit,
        s3_destination_folder=(bucket, prefix),
        shots=plan.shots,
        # The program is wrapped in a verbatim box, which already tells the service to run the
        # physical qubits as written. Asking for disable_qubit_rewiring as well is rejected.
        disable_qubit_rewiring=False,
        tags=plan.report.tags,
    )

    directory = _submission_dir(plan, moment, run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    results_location = {"bucket": bucket, "prefix": prefix}
    record = _submission_record(
        plan=plan,
        task_arn=task.id,
        task_status=str(task.state()),
        moment=moment,
        caller=identity,
        results_location=results_location,
    )
    record_path = directory / "submission.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return SubmissionResult(
        task_arn=task.id,
        task_status=str(record["task"]["status_at_creation"]),
        submitted_at=moment.isoformat(),
        plan=plan,
        caller=identity,
        results_location=results_location,
        record_path=record_path,
    )
