# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Validated records: the receipt a circuit needs before it may reach a QPU.

A record says that one specific program, identified by
:func:`~shor_braket.gate.circuit_hash.circuit_hash`, was accepted by the verbatim validators of
one device and kept enough of the period signal under that device's calibration noise. Records
are small manifests and are committed, so the history of "what was checked before it was run"
lives in git. The measurement data itself stays in the gitignored run artifact, which the record
points at.

Two fields decide whether an old record still means anything:

* ``circuit_hash`` — edit the circuit and the hash moves, so no record is found at all;
* ``snapshot.capabilities_sha256`` — the calibration the emulation was based on. IQM recalibrates
  daily, and a signal fraction measured against week-old numbers is not a statement about the
  machine that would run the task today.

The time limit is the weaker of the three checks and exists for the case where nothing else moved.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from shor_braket.gate.circuit_hash import CANONICAL_FORM_VERSION, hash_digest

RECORD_SCHEMA_VERSION = 1
DEFAULT_VALIDITY_DAYS = 30
DEFAULT_RECORD_DIR = Path("runs/validated")


@dataclass(frozen=True)
class ValidatedRecord:
    """One circuit's proof of local validation."""

    circuit_hash: str
    issued_at: str
    expires_at: str
    device: dict[str, Any]
    snapshot: dict[str, Any]
    problem: dict[str, Any]
    oracle_mode: str
    circuit: dict[str, Any]
    emulation: dict[str, Any]
    environment: dict[str, Any]
    artifact: str | None = None
    git_commit: str | None = None
    schema_version: int = RECORD_SCHEMA_VERSION
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """Return the bare hex digest of the circuit hash."""
        return hash_digest(self.circuit_hash)

    @property
    def device_key(self) -> str:
        """Return the logical device name the record was issued for."""
        return str(self.device["key"])

    @property
    def device_arn(self) -> str:
        """Return the device ARN the record was issued for."""
        return str(self.device["arn"])

    def expires_at_datetime(self) -> datetime:
        """Parse the expiry timestamp."""
        return datetime.fromisoformat(self.expires_at)

    def is_expired(self, now: datetime | None = None) -> bool:
        """Report whether the record is past its expiry.

        Args:
            now: Point in time to compare against; defaults to the current UTC time.

        Returns:
            ``True`` when the record has expired.
        """
        return (now or datetime.now(UTC)) > self.expires_at_datetime()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record for JSON."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ValidatedRecord:
        """Deserialize a record written by :func:`save_record`.

        Args:
            data: Parsed JSON of a record file.

        Returns:
            The record.

        Raises:
            ValueError: If the schema version is not the one this code writes.
        """
        version = data.get("schema_version")
        if version != RECORD_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported validated record schema version {version!r}; "
                f"expected {RECORD_SCHEMA_VERSION}"
            )
        fields = {name: data[name] for name in cls.__dataclass_fields__ if name in data}
        return cls(**fields)


def _git_commit() -> str | None:
    """Return the commit the run was made from, if the caller passed one in the environment."""
    commit = os.environ.get("GIT_COMMIT", "").strip()
    return commit or None


def issue_record(
    *,
    circuit_hash_value: str,
    device: dict[str, Any],
    snapshot: dict[str, Any],
    problem: dict[str, Any],
    oracle_mode: str,
    circuit: dict[str, Any],
    emulation: dict[str, Any],
    environment: dict[str, Any],
    artifact: str | None = None,
    issued_at: datetime | None = None,
    validity_days: int = DEFAULT_VALIDITY_DAYS,
    notes: dict[str, Any] | None = None,
) -> ValidatedRecord:
    """Build a record for a circuit that passed local validation.

    Args:
        circuit_hash_value: Hash of the exact verbatim program that was emulated.
        device: Logical key, ARN and display name of the target device.
        snapshot: Calibration snapshot identity: hash, calibration time, fetch time.
        problem: Modulus, base, register sizes.
        oracle_mode: Which oracle the circuit uses, recorded so that a compiled oracle can never
            pass unnoticed.
        circuit: Structural facts about the routed, lowered program.
        emulation: Shots, metrics and the pass verdict of the emulation.
        environment: Versions of the SDK and simulator the emulation ran on.
        artifact: Path of the run directory that holds the full data.
        issued_at: Issue time; defaults to the current UTC time.
        validity_days: How long the record stays valid.
        notes: Free-form extra context.

    Returns:
        The record, not yet written to disk.

    Raises:
        ValueError: If ``validity_days`` is not positive.
    """
    if validity_days < 1:
        raise ValueError("validity_days must be positive")
    when = issued_at or datetime.now(UTC)
    return ValidatedRecord(
        circuit_hash=circuit_hash_value,
        issued_at=when.isoformat(),
        expires_at=(when + timedelta(days=validity_days)).isoformat(),
        device=dict(device),
        snapshot=dict(snapshot),
        problem=dict(problem),
        oracle_mode=oracle_mode,
        circuit={"canonical_form_version": CANONICAL_FORM_VERSION, **circuit},
        emulation=dict(emulation),
        environment=dict(environment),
        artifact=artifact,
        git_commit=_git_commit(),
        notes=dict(notes or {}),
    )


def record_path(circuit_hash_value: str, directory: Path = DEFAULT_RECORD_DIR) -> Path:
    """Return the file a record for this circuit hash lives in.

    Args:
        circuit_hash_value: Hash in the ``"sha256:<hex>"`` form.
        directory: Record directory.

    Returns:
        The path, named by the bare hex digest so lookup needs no directory scan.
    """
    return directory / f"{hash_digest(circuit_hash_value)}.json"


def save_record(record: ValidatedRecord, directory: Path = DEFAULT_RECORD_DIR) -> Path:
    """Write a record as pretty-printed JSON and return its path.

    Args:
        record: The record to write.
        directory: Record directory, created if missing.

    Returns:
        The path written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = record_path(record.circuit_hash, directory)
    path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def load_record(circuit_hash_value: str, directory: Path = DEFAULT_RECORD_DIR) -> ValidatedRecord:
    """Load the record for one circuit hash.

    Args:
        circuit_hash_value: Hash in the ``"sha256:<hex>"`` form.
        directory: Record directory.

    Returns:
        The record.

    Raises:
        FileNotFoundError: If no record was ever issued for this circuit.
        ValueError: If the file exists but was issued for a different circuit.
    """
    path = record_path(circuit_hash_value, directory)
    if not path.is_file():
        raise FileNotFoundError(
            f"no validated record for {circuit_hash_value} at {path}; "
            "run `make validate-n15` for this device and oracle first"
        )
    record = ValidatedRecord.from_dict(json.loads(path.read_text("utf-8")))
    if record.circuit_hash != circuit_hash_value:
        raise ValueError(
            f"{path} holds a record for {record.circuit_hash}, not {circuit_hash_value}"
        )
    return record


def iter_records(directory: Path = DEFAULT_RECORD_DIR) -> Iterator[ValidatedRecord]:
    """Yield every readable record in a directory, in file-name order.

    Args:
        directory: Record directory.

    Yields:
        Each record that parses. Unreadable files are skipped so one bad file cannot hide the
        rest of the inventory.
    """
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            yield ValidatedRecord.from_dict(json.loads(path.read_text("utf-8")))
        except (ValueError, KeyError, TypeError):
            continue
