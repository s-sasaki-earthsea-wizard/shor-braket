# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""The preflight that runs between building a circuit and paying for a quantum task.

Every check here is client-side, which means every check here is bypassable by anyone who calls
the SDK directly. That is the point of the layering in ``docs/03-execution-gate.md``: this module
is the accident guard, and the IAM deny list plus the Braket spending limit are the security
boundary. Do not move a check out of AWS and into this file.

The checks fall into three groups:

* **identity** — is this exact program the one a validated record was issued for, on this device,
  against calibration that is still current;
* **eligibility** — is the program even a QPU program. The dense-matrix reference circuit of
  Phase 1 is refused here explicitly, before any device is contacted;
* **money** — shots inside the device's range, estimate under the per-task ceiling, and estimate
  inside what the service-side spending limit still allows.

A spending limit that cannot be read is never treated as headroom: unknown blocks, exactly like
insufficient.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Any

from braket.circuits import Circuit

from shor_braket.cost import QPU_CANDIDATES, qpu_cost_estimates
from shor_braket.devices.snapshot import DeviceSnapshot
from shor_braket.gate.circuit_hash import CANONICAL_FORM_VERSION, circuit_hash
from shor_braket.gate.record import DEFAULT_RECORD_DIR, ValidatedRecord, load_record
from shor_braket.gate.spending import (
    SpendingLimitLookup,
    SpendingLimitStatus,
    no_spending_limit_lookup,
)
from shor_braket.gate.tags import build_tags

DEFAULT_MAX_COST_USD = Decimal("10")
DENSE_MATRIX_GATES = frozenset({"unitary"})
VERBATIM_START = "startverbatimbox"
VERBATIM_END = "endverbatimbox"


@dataclass(frozen=True)
class Check:
    """One preflight question and its answer."""

    name: str
    passed: bool
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize the check."""
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass
class PreflightReport:
    """Everything the operator should see before answering the confirmation prompt."""

    device_key: str
    device_arn: str
    device_name: str
    shots: int
    circuit_hash: str
    checks: list[Check] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    spending_limit: dict[str, Any] | None = None
    record: ValidatedRecord | None = None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Report whether every blocking check passed."""
        return all(check.passed for check in self.checks if check.blocking)

    @property
    def blockers(self) -> list[Check]:
        """Return the blocking checks that failed."""
        return [check for check in self.checks if check.blocking and not check.passed]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report."""
        return {
            "device": {"key": self.device_key, "arn": self.device_arn, "name": self.device_name},
            "shots": self.shots,
            "circuit_hash": self.circuit_hash,
            "passed": self.passed,
            "checks": [check.to_dict() for check in self.checks],
            "cost": self.cost,
            "tags": self.tags,
            "spending_limit": self.spending_limit,
            "validated_record": {
                "issued_at": self.record.issued_at,
                "expires_at": self.record.expires_at,
                "artifact": self.record.artifact,
                "git_commit": self.record.git_commit,
            }
            if self.record is not None
            else None,
        }

    def render(self) -> str:
        """Format the report the way ``docs/03-execution-gate.md`` §5 shows it."""
        lines = [
            f"[gate] device                   {self.device_name} ({self.device_key})",
            f"[gate] circuit_hash             {self.circuit_hash}",
        ]
        for check in self.checks:
            mark = "OK  " if check.passed else ("FAIL" if check.blocking else "WARN")
            lines.append(f"[gate] {check.name:<24} {mark}  {check.detail}")
        lines.append(f"[cost] arn                      {self.device_arn}")
        lines.append(f"[cost] shots                    {self.shots}")
        lines.append(f"[cost] estimated                {self.cost.get('estimated_cost_usd')} USD")
        lines.append(f"[cost] per-task ceiling         {self.cost.get('max_cost_usd')} USD")
        if self.tags:
            rendered_tags = " ".join(f"{key}={value}" for key, value in self.tags.items())
            lines.append(f"[cost] tags                     {rendered_tags}")
        if self.spending_limit is None:
            lines.append("[cost] spending limit           unavailable (issue #3)")
        else:
            limit = self.spending_limit
            lines.append(f"[cost] spending limit           {limit.get('limit_usd')} USD")
            lines.append(
                f"[cost] current + queued         {limit.get('current_spend_usd')} "
                f"+ {limit.get('queued_spend_usd')} USD"
            )
            lines.append(f"[cost] remaining                {limit.get('remaining_usd')} USD")
            lines.append(
                f"[cost] active period            {limit.get('active_from')} "
                f"-> {limit.get('active_to')}"
            )
        verdict = "PASS" if self.passed else "REFUSED"
        lines.append(f"[gate] verdict                  {verdict}")
        for check in self.blockers:
            lines.append(f"[gate]   blocked by             {check.name}: {check.detail}")
        return "\n".join(lines)


def circuit_eligibility(circuit: Circuit) -> tuple[bool, str]:
    """Check that a circuit is shaped like a hardware program at all.

    This is deliberately not a re-implementation of the emulator's validators; the authoritative
    acceptance is the one recorded in the validated record. It catches the two things that make a
    circuit obviously unsubmittable, which is what the Phase 1 reference circuit trips on.

    Args:
        circuit: The circuit about to be submitted.

    Returns:
        ``(True, "")`` when the circuit may proceed, otherwise ``(False, reason)``.
    """
    names = [str(instruction.operator.name).lower() for instruction in circuit.instructions]
    dense = sorted({name for name in names if name in DENSE_MATRIX_GATES})
    if dense:
        return False, (
            f"circuit contains dense matrix gates {dense}, which have no native decomposition; "
            "this is the local-reference circuit and is never QPU eligible (docs/adr/0003)"
        )
    if VERBATIM_START not in names:
        return False, (
            "circuit is not wrapped in a verbatim box, so the device would transpile it and the "
            "validated program would not be the program that runs"
        )
    if names.count(VERBATIM_START) != names.count(VERBATIM_END):
        return False, "verbatim box markers are unbalanced"
    start, end = names.index(VERBATIM_START), len(names) - 1 - names[::-1].index(VERBATIM_END)
    outside = [
        name
        for index, name in enumerate(names)
        if not start <= index <= end and name not in {VERBATIM_START, VERBATIM_END}
    ]
    if outside:
        return False, f"instructions outside the verbatim box would be transpiled: {outside}"
    return True, ""


def _max_cost_usd() -> Decimal:
    """Read the per-task ceiling from the environment, falling back to the documented default."""
    raw = os.environ.get("BRAKET_MAX_COST_USD", "").strip()
    if not raw:
        return DEFAULT_MAX_COST_USD
    try:
        return Decimal(raw)
    except ArithmeticError:
        return DEFAULT_MAX_COST_USD


def _major(version_string: str) -> str:
    """Return the leading component of a version string."""
    return version_string.split(".", 1)[0]


def _identity_checks(
    record: ValidatedRecord,
    *,
    device_arn: str,
    snapshot: DeviceSnapshot,
    now: datetime,
) -> list[Check]:
    """Check that the record still describes this circuit on this device today."""
    checks: list[Check] = []

    recorded_arn = record.device_arn
    checks.append(
        Check(
            name="device match",
            passed=recorded_arn == device_arn,
            detail=(
                f"record issued for {recorded_arn}"
                if recorded_arn != device_arn
                else "record and target are the same device"
            ),
        )
    )

    recorded_calibration = str(record.snapshot.get("capabilities_sha256", ""))
    calibration_current = recorded_calibration == snapshot.capabilities_sha256
    checks.append(
        Check(
            name="calibration current",
            passed=calibration_current,
            detail=(
                f"calibration {snapshot.calibration_updated_at} still matches the emulation"
                if calibration_current
                else (
                    "the device was recalibrated after the emulation; re-run the emulation "
                    f"(record {recorded_calibration[:23]}..., snapshot "
                    f"{snapshot.capabilities_sha256[:23]}...)"
                )
            ),
        )
    )

    expired = record.is_expired(now)
    checks.append(
        Check(
            name="record fresh",
            passed=not expired,
            detail=(
                f"issued {record.issued_at}, expires {record.expires_at}"
                if not expired
                else f"expired at {record.expires_at}"
            ),
        )
    )

    recorded_sdk = str(record.environment.get("amazon_braket_sdk", ""))
    current_sdk = version("amazon-braket-sdk")
    sdk_ok = _major(recorded_sdk) == _major(current_sdk)
    checks.append(
        Check(
            name="sdk major match",
            passed=sdk_ok,
            detail=(
                f"validated on {recorded_sdk}, running {current_sdk}"
                if not sdk_ok
                else f"{current_sdk}"
            ),
        )
    )

    recorded_form = record.circuit.get("canonical_form_version")
    form_ok = recorded_form == CANONICAL_FORM_VERSION
    checks.append(
        Check(
            name="hash form match",
            passed=form_ok,
            detail=(
                f"canonical form v{CANONICAL_FORM_VERSION}"
                if form_ok
                else f"record uses canonical form v{recorded_form}; re-issue it"
            ),
        )
    )

    verdict = record.emulation.get("verdict") or {}
    passed = bool(verdict.get("passed"))
    signal = verdict.get("signal_fraction_exact")
    checks.append(
        Check(
            name="emulation verdict",
            passed=passed,
            detail=(
                f"signal fraction {signal:.3f} >= {verdict.get('pass_line')}"
                if passed and isinstance(signal, float)
                else f"emulation did not pass: {verdict or 'no verdict recorded'}"
            ),
        )
    )
    return checks


def preflight(
    circuit: Circuit,
    *,
    device_key: str,
    shots: int,
    snapshot: DeviceSnapshot,
    record_dir: Path = DEFAULT_RECORD_DIR,
    max_cost_usd: Decimal | None = None,
    spending_lookup: SpendingLimitLookup = no_spending_limit_lookup,
    now: datetime | None = None,
    campaign: str | None = None,
) -> PreflightReport:
    """Run every client-side check that stands between a circuit and a paid task.

    Args:
        circuit: The exact program that would be submitted, verbatim box included.
        device_key: Logical device name, resolved to an ARN through the approved candidate list
            so that a mistyped ARN cannot select a more expensive machine.
        shots: Shots the task would request.
        snapshot: The calibration snapshot on disk right now, used to detect a recalibration
            since the emulation.
        record_dir: Where validated records live.
        max_cost_usd: Per-task ceiling; defaults to ``BRAKET_MAX_COST_USD`` or 10 USD.
        spending_lookup: How to read the service-side spending limit. The default reports no
            knowledge, which blocks.
        now: Point in time for the freshness checks; defaults to the current UTC time.
        campaign: Optional campaign tag for cost grouping. It grants nothing: AQT is reached by
            assuming a different role, not by setting a tag (ADR-0004).

    Returns:
        The report. Inspect :attr:`PreflightReport.passed` before submitting anything.

    Raises:
        ValueError: If the device key is not one of the approved candidates or shots is not
            positive.
    """
    if device_key not in QPU_CANDIDATES:
        raise ValueError(
            f"unknown device key {device_key!r}; approved devices are {sorted(QPU_CANDIDATES)}"
        )
    if shots < 1:
        raise ValueError("shots must be positive")

    candidate = QPU_CANDIDATES[device_key]
    moment = now or datetime.now(UTC)
    ceiling = max_cost_usd if max_cost_usd is not None else _max_cost_usd()
    program_hash = circuit_hash(circuit)
    report = PreflightReport(
        device_key=device_key,
        device_arn=candidate.arn,
        device_name=candidate.name,
        shots=shots,
        circuit_hash=program_hash,
    )

    eligible, reason = circuit_eligibility(circuit)
    report.checks.append(
        Check(
            name="qpu eligible circuit",
            passed=eligible,
            detail=reason or "verbatim program with no dense matrix gates",
        )
    )

    if snapshot.arn != candidate.arn:
        report.checks.append(
            Check(
                name="snapshot device",
                passed=False,
                detail=f"snapshot is for {snapshot.arn}, target is {candidate.arn}",
            )
        )
    else:
        report.checks.append(
            Check(name="snapshot device", passed=True, detail=f"snapshot for {snapshot.key}")
        )

    record: ValidatedRecord | None = None
    try:
        record = load_record(program_hash, record_dir)
    except (FileNotFoundError, ValueError) as error:
        report.checks.append(Check(name="validated record", passed=False, detail=str(error)))

    if record is not None:
        report.record = record
        report.checks.append(
            Check(
                name="validated record",
                passed=True,
                detail=f"{record.device_key} / {record.oracle_mode}, issued {record.issued_at}",
            )
        )
        report.checks.extend(
            _identity_checks(record, device_arn=candidate.arn, snapshot=snapshot, now=moment)
        )

    if record is not None:
        try:
            report.tags = build_tags(oracle_mode=record.oracle_mode, campaign=campaign)
        except ValueError as error:
            report.checks.append(Check(name="tags", passed=False, detail=str(error)))

    if device_key == "ibex":
        report.checks.append(
            Check(
                name="aqt needs its own role",
                passed=True,
                blocking=False,
                detail=(
                    "submit with AWS_PROFILE_AQT; the everyday execution role denies AQT "
                    "unconditionally (ADR-0004)"
                ),
            )
        )

    shots_ok = candidate.min_shots <= shots <= candidate.max_shots
    report.checks.append(
        Check(
            name="shots in range",
            passed=shots_ok,
            detail=(
                f"{shots} within [{candidate.min_shots}, {candidate.max_shots}]"
                if shots_ok
                else f"{shots} outside [{candidate.min_shots}, {candidate.max_shots}]"
            ),
        )
    )

    estimate_row = next(row for row in qpu_cost_estimates(shots) if row["device"] == device_key)
    estimated = Decimal(str(estimate_row["estimated_cost_usd"]))
    report.cost = {
        "estimated_cost_usd": str(estimated),
        "max_cost_usd": str(ceiling),
        "price_per_task_usd": str(candidate.price_per_task_usd),
        "price_per_shot_usd": str(candidate.price_per_shot_usd),
        "shots": shots,
    }
    report.checks.append(
        Check(
            name="cost under ceiling",
            passed=estimated <= ceiling,
            detail=f"{estimated} USD against a ceiling of {ceiling} USD",
        )
    )

    status: SpendingLimitStatus | None = spending_lookup(candidate.arn)
    if status is None:
        report.checks.append(
            Check(
                name="spending limit",
                passed=False,
                detail=(
                    "no spending limit could be read for this device. An unreadable limit is not "
                    "headroom; create it with Terraform (issue #3) and grant "
                    "braket:SearchSpendingLimits"
                ),
            )
        )
    else:
        report.spending_limit = status.to_dict()
        active = status.is_active(moment)
        report.checks.append(
            Check(
                name="spending limit active",
                passed=active,
                detail=(
                    f"in force {status.active_from} -> {status.active_to}"
                    if active
                    else f"outside the limit's period {status.active_from} -> {status.active_to}"
                ),
            )
        )
        room = status.remaining_usd >= estimated
        report.checks.append(
            Check(
                name="spending limit room",
                passed=room,
                detail=f"{status.remaining_usd} USD remaining against {estimated} USD estimated",
            )
        )

    return report
