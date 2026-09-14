# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Tests for the validated record and the submission gate.

Nothing here touches AWS. The spending limit, the only part of the gate that needs a live
service, is injected as a stub so that every refusal path can be exercised offline.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from braket.circuits import Circuit
from typer.testing import CliRunner

from shor_braket.cli import app
from shor_braket.devices import DEFAULT_SNAPSHOT_DIR
from shor_braket.gate.circuit_hash import (
    CANONICAL_FORM_VERSION,
    canonical_form,
    circuit_hash,
    hash_digest,
)
from shor_braket.gate.preflight import circuit_eligibility, preflight
from shor_braket.gate.record import (
    RECORD_SCHEMA_VERSION,
    ValidatedRecord,
    issue_record,
    iter_records,
    load_record,
    record_path,
    save_record,
)
from shor_braket.gate.spending import SpendingLimitStatus, spending_limit_from_api
from shor_braket.quantum.native import verbatim
from shor_braket.quantum.reference import build_reference_circuit
from shor_braket.runner.submit import SubmissionPlan, plan_submission, submit

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
SNAPSHOT_DIR_FOR_TESTS = Path(__file__).resolve().parents[1] / DEFAULT_SNAPSHOT_DIR


def _native_program() -> Circuit:
    """A small verbatim program on physical qubits Garnet actually has."""
    body = Circuit().prx(10, 0.5, 0.25).cz(10, 15).prx(15, 1.0, 0.0)
    return verbatim(body)


def _record_for(
    program: Circuit,
    garnet_snapshot,
    *,
    device_key: str = "garnet",
    arn: str | None = None,
    capabilities_sha256: str | None = None,
    signal_fraction: float = 0.62,
    passed: bool = True,
    issued_at: datetime = NOW,
    validity_days: int = 30,
) -> ValidatedRecord:
    """Build a record for a program with every field the preflight inspects."""
    from importlib.metadata import version

    return issue_record(
        circuit_hash_value=circuit_hash(program),
        device={
            "key": device_key,
            "arn": arn or garnet_snapshot.arn,
            "name": "IQM Garnet",
        },
        snapshot={
            "capabilities_sha256": capabilities_sha256 or garnet_snapshot.capabilities_sha256,
            "calibration_updated_at": garnet_snapshot.calibration_updated_at,
            "fetched_at": garnet_snapshot.fetched_at,
        },
        problem={"modulus": 15, "base": 7, "count_qubits": 2, "work_qubits": 4},
        oracle_mode="generic-constant",
        circuit={"native_two_qubit": 1, "native_depth": 3},
        emulation={
            "shots": 2000,
            "verdict": {
                "passed": passed,
                "pass_line": 0.5,
                "signal_fraction_exact": signal_fraction,
            },
        },
        environment={"amazon_braket_sdk": version("amazon-braket-sdk")},
        issued_at=issued_at,
        validity_days=validity_days,
    )


def _stub_limit(remaining: str = "100", **kwargs) -> SpendingLimitStatus:
    """A spending limit with the requested headroom."""
    return SpendingLimitStatus(
        device_arn=kwargs.pop("device_arn", "arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet"),
        limit_usd=Decimal(remaining),
        current_spend_usd=Decimal("0"),
        queued_spend_usd=Decimal("0"),
        **kwargs,
    )


# --- circuit hash ------------------------------------------------------------------------


def test_target_order_is_part_of_the_identity():
    # Sorting the targets, as the first sketch in docs/03 did, would merge these two programs.
    assert circuit_hash(Circuit().cnot(0, 1)) != circuit_hash(Circuit().cnot(1, 0))


def test_verbatim_box_and_result_types_are_part_of_the_identity():
    bare = Circuit().h(0)
    assert circuit_hash(bare) != circuit_hash(verbatim(Circuit().h(0)))
    assert circuit_hash(bare) != circuit_hash(Circuit().h(0).probability(target=[0]))


def test_physical_placement_is_part_of_the_identity():
    assert circuit_hash(verbatim(Circuit().prx(10, 0.5, 0.25))) != circuit_hash(
        verbatim(Circuit().prx(11, 0.5, 0.25))
    )


def test_float_representation_noise_does_not_change_the_hash():
    left = Circuit().prx(0, 0.1 + 0.2, 0.0)
    right = Circuit().prx(0, 0.30000000000000004, -0.0)
    assert circuit_hash(left) == circuit_hash(right)


def test_angles_further_apart_than_the_rounding_still_differ():
    left = Circuit().prx(0, 0.5, 0.0)
    right = Circuit().prx(0, 0.5 + 1e-9, 0.0)
    assert circuit_hash(left) != circuit_hash(right)


def test_hash_is_stable_across_rebuilds_of_the_same_circuit():
    assert circuit_hash(_native_program()) == circuit_hash(_native_program())


def test_canonical_form_carries_its_version_and_omits_shots():
    form = canonical_form(_native_program())
    assert form["canonical_form_version"] == CANONICAL_FORM_VERSION
    assert "shots" not in json.dumps(form)


def test_hash_digest_rejects_anything_that_is_not_a_sha256_hash():
    assert len(hash_digest(circuit_hash(Circuit().h(0)))) == 64
    with pytest.raises(ValueError, match="expected"):
        hash_digest("md5:abc")
    with pytest.raises(ValueError, match="hex"):
        hash_digest("sha256:nothex")


# --- validated record --------------------------------------------------------------------


def test_record_round_trips_through_disk(tmp_path, garnet_snapshot):
    program = _native_program()
    record = _record_for(program, garnet_snapshot)
    path = save_record(record, tmp_path)
    assert path.name == f"{hash_digest(record.circuit_hash)}.json"
    assert load_record(record.circuit_hash, tmp_path) == record


def test_loading_a_record_that_was_never_issued_fails(tmp_path):
    with pytest.raises(FileNotFoundError, match="no validated record"):
        load_record(circuit_hash(Circuit().h(0)), tmp_path)


def test_a_record_file_holding_another_circuit_is_rejected(tmp_path, garnet_snapshot):
    record = _record_for(_native_program(), garnet_snapshot)
    other = circuit_hash(Circuit().h(0))
    record_path(other, tmp_path).parent.mkdir(parents=True, exist_ok=True)
    record_path(other, tmp_path).write_text(json.dumps(record.to_dict()), "utf-8")
    with pytest.raises(ValueError, match="holds a record for"):
        load_record(other, tmp_path)


def test_a_record_from_a_future_schema_is_rejected(tmp_path, garnet_snapshot):
    record = _record_for(_native_program(), garnet_snapshot)
    payload = record.to_dict() | {"schema_version": RECORD_SCHEMA_VERSION + 1}
    path = record_path(record.circuit_hash, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), "utf-8")
    with pytest.raises(ValueError, match="unsupported validated record schema"):
        load_record(record.circuit_hash, tmp_path)


def test_expiry_is_measured_from_the_issue_time(garnet_snapshot):
    record = _record_for(_native_program(), garnet_snapshot, validity_days=7)
    assert not record.is_expired(NOW + timedelta(days=6))
    assert record.is_expired(NOW + timedelta(days=8))


def test_iter_records_skips_unreadable_files(tmp_path, garnet_snapshot):
    save_record(_record_for(_native_program(), garnet_snapshot), tmp_path)
    (tmp_path / "broken.json").write_text("{not json", "utf-8")
    assert len(list(iter_records(tmp_path))) == 1


def test_issue_record_requires_a_positive_validity(garnet_snapshot):
    with pytest.raises(ValueError, match="validity_days"):
        _record_for(_native_program(), garnet_snapshot, validity_days=0)


# --- eligibility -------------------------------------------------------------------------


def test_the_dense_matrix_reference_circuit_is_refused():
    reference = build_reference_circuit(modulus=15, base=7, count_qubit_count=2)
    eligible, reason = circuit_eligibility(reference.circuit)
    assert not eligible
    assert "dense matrix" in reason


def test_a_circuit_without_a_verbatim_box_is_refused():
    eligible, reason = circuit_eligibility(Circuit().prx(10, 0.5, 0.25))
    assert not eligible
    assert "verbatim" in reason


def test_instructions_outside_the_verbatim_box_are_refused():
    program = verbatim(Circuit().prx(10, 0.5, 0.25))
    program.prx(11, 0.5, 0.25)
    eligible, reason = circuit_eligibility(program)
    assert not eligible
    assert "outside the verbatim box" in reason


def test_a_native_verbatim_program_is_eligible():
    assert circuit_eligibility(_native_program()) == (True, "")


# --- preflight ---------------------------------------------------------------------------


def _preflight(program, garnet_snapshot, record_dir, **kwargs):
    kwargs.setdefault("device_key", "garnet")
    kwargs.setdefault("shots", 2000)
    kwargs.setdefault("spending_lookup", lambda arn: _stub_limit())
    kwargs.setdefault("now", NOW)
    return preflight(program, snapshot=garnet_snapshot, record_dir=record_dir, **kwargs)


def _failed(report, name):
    return next(check for check in report.checks if check.name == name and not check.passed)


def test_a_fully_validated_circuit_passes(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(_record_for(program, garnet_snapshot), tmp_path)
    report = _preflight(program, garnet_snapshot, tmp_path)
    assert report.passed, report.render()
    assert report.record is not None


def test_a_circuit_with_no_record_is_refused(tmp_path, garnet_snapshot):
    report = _preflight(_native_program(), garnet_snapshot, tmp_path)
    assert not report.passed
    assert "no validated record" in _failed(report, "validated record").detail


def test_editing_the_circuit_invalidates_the_record(tmp_path, garnet_snapshot):
    save_record(_record_for(_native_program(), garnet_snapshot), tmp_path)
    edited = verbatim(Circuit().prx(10, 0.5, 0.25).cz(10, 15).prx(15, 1.0, 0.5))
    report = _preflight(edited, garnet_snapshot, tmp_path)
    assert not report.passed
    assert _failed(report, "validated record")


def test_a_record_issued_for_another_device_is_refused(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(
        _record_for(
            program,
            garnet_snapshot,
            device_key="emerald",
            arn="arn:aws:braket:eu-north-1::device/qpu/iqm/Emerald",
        ),
        tmp_path,
    )
    report = _preflight(program, garnet_snapshot, tmp_path)
    assert not report.passed
    assert "record issued for" in _failed(report, "device match").detail


def test_a_recalibrated_device_invalidates_the_record(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(
        _record_for(program, garnet_snapshot, capabilities_sha256="sha256:" + "0" * 64), tmp_path
    )
    report = _preflight(program, garnet_snapshot, tmp_path)
    assert not report.passed
    assert "recalibrated" in _failed(report, "calibration current").detail


def test_an_expired_record_is_refused(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(_record_for(program, garnet_snapshot, validity_days=1), tmp_path)
    report = _preflight(program, garnet_snapshot, tmp_path, now=NOW + timedelta(days=2))
    assert not report.passed
    assert "expired" in _failed(report, "record fresh").detail


def test_an_emulation_that_did_not_pass_cannot_be_submitted(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(
        _record_for(program, garnet_snapshot, signal_fraction=0.31, passed=False), tmp_path
    )
    report = _preflight(program, garnet_snapshot, tmp_path)
    assert not report.passed
    assert _failed(report, "emulation verdict")


def test_the_reference_circuit_is_refused_by_the_preflight(tmp_path, garnet_snapshot):
    reference = build_reference_circuit(modulus=15, base=7, count_qubit_count=2)
    report = _preflight(reference.circuit, garnet_snapshot, tmp_path)
    assert not report.passed
    assert "dense matrix" in _failed(report, "qpu eligible circuit").detail


def test_shots_outside_the_device_range_are_refused(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(_record_for(program, garnet_snapshot), tmp_path)
    report = _preflight(program, garnet_snapshot, tmp_path, shots=20_001)
    assert not report.passed
    assert "outside" in _failed(report, "shots in range").detail


def test_an_estimate_over_the_ceiling_is_refused(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(_record_for(program, garnet_snapshot), tmp_path)
    report = _preflight(
        program, garnet_snapshot, tmp_path, shots=20_000, max_cost_usd=Decimal("5")
    )
    assert not report.passed
    assert _failed(report, "cost under ceiling")


def test_an_unreadable_spending_limit_blocks(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(_record_for(program, garnet_snapshot), tmp_path)
    report = _preflight(program, garnet_snapshot, tmp_path, spending_lookup=lambda arn: None)
    assert not report.passed
    assert "not headroom" in _failed(report, "spending limit").detail


def test_a_spending_limit_without_room_blocks(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(_record_for(program, garnet_snapshot), tmp_path)
    report = _preflight(
        program, garnet_snapshot, tmp_path, spending_lookup=lambda arn: _stub_limit("1")
    )
    assert not report.passed
    assert _failed(report, "spending limit room")


def test_a_spending_limit_outside_its_period_blocks(tmp_path, garnet_snapshot):
    program = _native_program()
    save_record(_record_for(program, garnet_snapshot), tmp_path)
    lapsed = _stub_limit(
        active_from="2026-01-01T00:00:00+00:00", active_to="2026-02-01T00:00:00+00:00"
    )
    report = _preflight(program, garnet_snapshot, tmp_path, spending_lookup=lambda arn: lapsed)
    assert not report.passed
    assert _failed(report, "spending limit active")


def test_a_snapshot_for_another_device_blocks(tmp_path, ibex_snapshot):
    program = _native_program()
    report = preflight(
        program,
        device_key="garnet",
        shots=2000,
        snapshot=ibex_snapshot,
        record_dir=tmp_path,
        spending_lookup=lambda arn: _stub_limit(),
        now=NOW,
    )
    assert not report.passed
    assert _failed(report, "snapshot device")


def test_an_unapproved_device_key_is_rejected_outright(tmp_path, garnet_snapshot):
    with pytest.raises(ValueError, match="unknown device key"):
        preflight(
            _native_program(), device_key="ionq", shots=10, snapshot=garnet_snapshot,
            record_dir=tmp_path,
        )


def test_the_rendered_report_names_every_blocker(tmp_path, garnet_snapshot):
    report = _preflight(_native_program(), garnet_snapshot, tmp_path)
    rendered = report.render()
    assert "REFUSED" in rendered
    assert "blocked by" in rendered


# --- spending limit parsing --------------------------------------------------------------


def test_spending_limit_remaining_never_goes_negative():
    status = SpendingLimitStatus(
        device_arn="arn",
        limit_usd=Decimal("10"),
        current_spend_usd=Decimal("8"),
        queued_spend_usd=Decimal("5"),
    )
    assert status.remaining_usd == Decimal("0")


def test_spending_limit_is_parsed_without_binary_floats():
    status = spending_limit_from_api(
        {
            "deviceArn": "arn",
            "spendingLimit": 10.1,
            "currentSpend": 0.2,
            "queuedSpend": 0,
            "timePeriod": {"start": "2026-09-01T00:00:00+00:00", "end": None},
        }
    )
    assert status.remaining_usd == Decimal("9.9")


# --- submission path ---------------------------------------------------------------------


def test_submission_refuses_without_a_record(tmp_path, snapshot_dir):
    plan = plan_submission(
        device_key="garnet",
        oracle_mode="generic-constant",
        shots=1000,
        snapshot_dir=snapshot_dir,
        record_dir=tmp_path,
        max_permutations=1,
        now=NOW,
    )
    assert isinstance(plan, SubmissionPlan)
    assert not plan.allowed


def test_submit_always_refuses_to_create_a_task(tmp_path, snapshot_dir):
    plan = plan_submission(
        device_key="garnet",
        oracle_mode="generic-constant",
        shots=1000,
        snapshot_dir=snapshot_dir,
        record_dir=tmp_path,
        max_permutations=1,
        now=NOW,
    )
    with pytest.raises(NotImplementedError, match="issue #3"):
        submit(plan)


def test_submission_rejects_an_unapproved_device(tmp_path, snapshot_dir):
    with pytest.raises(ValueError, match="unknown device key"):
        plan_submission(
            device_key="ionq",
            oracle_mode="generic-constant",
            shots=10,
            snapshot_dir=snapshot_dir,
            record_dir=tmp_path,
        )


# --- CLI ---------------------------------------------------------------------------------


def test_yes_without_a_ceiling_is_rejected():
    result = CliRunner().invoke(
        app, ["submit-qpu", "--device", "garnet", "--shots", "1000", "--yes"]
    )
    assert result.exit_code == 2
    assert "--max-cost" in result.output


def test_records_command_lists_what_is_on_disk(tmp_path, garnet_snapshot):
    save_record(_record_for(_native_program(), garnet_snapshot), tmp_path)
    result = CliRunner().invoke(app, ["records", "--record-dir", str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["records"][0]["device"] == "garnet"


def test_the_submission_rebuild_hashes_to_what_the_emulator_validated(tmp_path, garnet_snapshot):
    """The whole gate rests on this: rebuilding must reproduce the emulated program exactly."""
    from shor_braket.devices import summarize_snapshot
    from shor_braket.runner.n15 import emulate_n15_configuration
    from shor_braket.runner.submit import build_submission_circuit

    emulated = emulate_n15_configuration(
        garnet_snapshot,
        summarize_snapshot(garnet_snapshot),
        oracle_mode="generic-constant",
        shots=1,
        max_permutations=1,
    )
    rebuilt = build_submission_circuit(
        device_key="garnet",
        oracle_mode="generic-constant",
        snapshot_dir=SNAPSHOT_DIR_FOR_TESTS,
        max_permutations=1,
    )
    assert circuit_hash(rebuilt) == emulated["circuit_hash"]
