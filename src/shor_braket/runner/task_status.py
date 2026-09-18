# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Ask the service what happened to the tasks this project created.

Checking on a task is a read, so it runs under the read-only profile: no assume-role step, no
MFA prompt, nothing that could create work. ``GetQuantumTask`` is free.

The tasks to ask about come from the submission records that :mod:`shor_braket.runner.submit`
writes, which means this reports on what this repository actually paid for rather than on
everything in the account. A task created by some other means will not appear, and that is the
intended reading: the records are the project's own ledger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shor_braket.runner.submit import DEFAULT_RUN_DIR

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    import boto3

# States from which a task will not move again. Anything else may still change.
TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


@dataclass(frozen=True)
class SubmittedTask:
    """One task this repository created, as its submission record describes it."""

    task_arn: str
    device_key: str
    oracle_mode: str
    shots: int
    circuit_hash: str
    submitted_at: str
    estimated_cost_usd: str | None
    record_path: Path

    @classmethod
    def from_record(cls, path: Path) -> SubmittedTask:
        """Read one submission record.

        Args:
            path: Path of a ``submission.json``.

        Returns:
            The task it describes.

        Raises:
            ValueError: If the file is not a submission record this code understands.
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        task = data.get("task") or {}
        arn = task.get("arn")
        if not arn:
            raise ValueError(f"{path} has no task ARN; it is not a submission record")
        return cls(
            task_arn=str(arn),
            device_key=str((data.get("device") or {}).get("key", "")),
            oracle_mode=str(data.get("oracle_mode", "")),
            shots=int(data.get("shots", 0)),
            circuit_hash=str(data.get("circuit_hash", "")),
            submitted_at=str(data.get("submitted_at", "")),
            estimated_cost_usd=(data.get("cost") or {}).get("estimated_cost_usd"),
            record_path=path,
        )


def iter_submissions(run_dir: Path = DEFAULT_RUN_DIR) -> list[SubmittedTask]:
    """Collect every submission record on disk, oldest first.

    Args:
        run_dir: Directory holding the raw run artifacts.

    Returns:
        The tasks, sorted by submission time. Unreadable records are skipped rather than
        aborting the listing: one corrupt file should not hide the rest of the ledger.
    """
    tasks: list[SubmittedTask] = []
    for path in sorted(run_dir.glob("qpu-*/submission.json")):
        try:
            tasks.append(SubmittedTask.from_record(path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return sorted(tasks, key=lambda task: task.submitted_at)


def _as_text(value: object) -> str | None:
    """Render an API value that may be a datetime as a string."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def describe_task(session: boto3.Session, task_arn: str) -> dict[str, Any]:
    """Read one task's current state from the service.

    Args:
        session: A read-only boto3 session.
        task_arn: The task to ask about.

    Returns:
        The fields worth showing, with timestamps normalized to strings.
    """
    response = session.client("braket").get_quantum_task(quantumTaskArn=task_arn)
    output_bucket = response.get("outputS3Bucket")
    output_directory = response.get("outputS3Directory")
    status = str(response.get("status", ""))
    return {
        "task_arn": task_arn,
        "status": status,
        "terminal": status in TERMINAL_STATES,
        "device_arn": response.get("deviceArn"),
        "shots": response.get("shots"),
        "created_at": _as_text(response.get("createdAt")),
        "ended_at": _as_text(response.get("endedAt")),
        "failure_reason": response.get("failureReason"),
        "results": {"bucket": output_bucket, "directory": output_directory}
        if output_bucket
        else None,
    }


def task_status_report(
    session: boto3.Session,
    *,
    task_arn: str | None = None,
    run_dir: Path = DEFAULT_RUN_DIR,
) -> dict[str, Any]:
    """Report on one task, or on every task this repository has created.

    Args:
        session: A read-only boto3 session.
        task_arn: A single task to ask about. When omitted, every submission record is used.
        run_dir: Directory holding the raw run artifacts.

    Returns:
        The report, with one entry per task. Each entry carries what the submission record said
        alongside what the service says now, so a task whose record is missing is visible as
        such rather than silently equivalent to one with a record.
    """
    if task_arn is not None:
        return {"tasks": [describe_task(session, task_arn)], "count": 1}

    submissions = iter_submissions(run_dir)
    entries: list[dict[str, Any]] = []
    for submission in submissions:
        entry: dict[str, Any] = {
            "submitted_at": submission.submitted_at,
            "device": submission.device_key,
            "oracle_mode": submission.oracle_mode,
            "circuit_hash": submission.circuit_hash,
            "estimated_cost_usd": submission.estimated_cost_usd,
            "submission_record": str(submission.record_path),
        }
        entry.update(describe_task(session, submission.task_arn))
        entries.append(entry)
    return {"run_dir": str(run_dir), "count": len(entries), "tasks": entries}
