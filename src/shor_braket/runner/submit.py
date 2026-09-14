# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Assemble a QPU submission and stop it at the preflight.

This module builds the same program the emulation built, runs it through
:func:`~shor_braket.gate.preflight.preflight`, and hands back a decision. It does not create a
quantum task: the AWS side of the guardrails (results bucket, budgets, spending limits) is
issue #3, and the last gate a circuit passes must be the service-side one, not this file.

Keeping the assembly here, separate from the emulation runner, is what makes the hash check
meaningful. The program is rebuilt from the same inputs, so if anything about the circuit, the
routing or the lowering changed since the record was issued, the rebuilt hash simply will not
match the record and the preflight refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from braket.circuits import Circuit

from shor_braket.cost import QPU_CANDIDATES
from shor_braket.devices.calibration import summarize_snapshot
from shor_braket.devices.snapshot import DEFAULT_SNAPSHOT_DIR, load_snapshot
from shor_braket.gate.preflight import PreflightReport, preflight
from shor_braket.gate.record import DEFAULT_RECORD_DIR
from shor_braket.gate.spending import SpendingLimitLookup, no_spending_limit_lookup
from shor_braket.quantum.compile import compile_to_native
from shor_braket.quantum.n15 import ORACLE_MODES, build_n15_circuit
from shor_braket.quantum.native import gate_family, verbatim
from shor_braket.quantum.routing import choose_layout

SUBMISSION_BLOCKED_REASON = (
    "Submission is not implemented. The AWS guardrails it depends on (results bucket, budgets "
    "and per-device spending limits) are issue #3, and a task must not be created before the "
    "service-side stop exists. See docs/03-execution-gate.md."
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
                "reason": SUBMISSION_BLOCKED_REASON,
            },
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
    )
    return SubmissionPlan(
        device_key=device_key,
        oracle_mode=oracle_mode,
        shots=shots,
        circuit=circuit,
        report=report,
    )


def submit(plan: SubmissionPlan) -> None:
    """Refuse to create a quantum task.

    Args:
        plan: A plan whose preflight has already been inspected.

    Raises:
        NotImplementedError: Always. The client-side gate is in place, but the service-side
            guardrails it must sit behind are issue #3.
    """
    del plan
    raise NotImplementedError(SUBMISSION_BLOCKED_REASON)
