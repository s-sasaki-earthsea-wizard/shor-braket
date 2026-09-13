# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Offline snapshots of Amazon Braket device capabilities.

A snapshot is the ``deviceCapabilities`` payload of one ``braket:GetDevice`` call, wrapped with
the metadata needed to reproduce a local emulation later: device ARN, fetch time, calibration
time, and a hash of the payload. Fetching needs read-only AWS access; everything after that runs
offline inside the Docker container.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shor_braket.cost import QPU_CANDIDATES

SNAPSHOT_SCHEMA_VERSION = 1
DEFAULT_SNAPSHOT_DIR = Path("devices/snapshots")


@dataclass(frozen=True)
class DeviceSnapshot:
    """Device capabilities captured at one point in time."""

    key: str
    arn: str
    name: str
    provider: str
    device_type: str
    status_at_fetch: str
    fetched_at: str
    calibration_updated_at: str | None
    capabilities_sha256: str
    capabilities: dict[str, Any]

    @property
    def capabilities_json(self) -> str:
        """Return the payload in the form ``LocalEmulator.from_json`` expects."""
        return json.dumps(self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the snapshot including its schema version."""
        return {"schema_version": SNAPSHOT_SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceSnapshot:
        """Deserialize a snapshot written by :func:`save_snapshot`."""
        version = data.get("schema_version")
        if version != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported snapshot schema version {version!r}; "
                f"expected {SNAPSHOT_SCHEMA_VERSION}"
            )
        fields = {name: data[name] for name in cls.__dataclass_fields__}
        return cls(**fields)


def capabilities_hash(capabilities: dict[str, Any]) -> str:
    """Hash the canonical JSON form of a capabilities payload."""
    canonical = json.dumps(capabilities, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def snapshot_from_get_device(
    response: dict[str, Any], *, key: str, fetched_at: datetime | None = None
) -> DeviceSnapshot:
    """Build a snapshot from a raw ``GetDevice`` response.

    Args:
        response: Parsed JSON of ``aws braket get-device``.
        key: Logical device name from :data:`shor_braket.cost.QPU_CANDIDATES`.
        fetched_at: Fetch time; defaults to now in UTC.

    Returns:
        The snapshot with transient fields such as queue depth dropped.

    Raises:
        ValueError: If the key is unknown, the ARN does not match the approved device, or the
            capabilities payload is malformed.
    """
    if key not in QPU_CANDIDATES:
        raise ValueError(f"unknown device key {key!r}; expected one of {sorted(QPU_CANDIDATES)}")
    expected_arn = QPU_CANDIDATES[key].arn
    arn = response.get("deviceArn")
    if arn != expected_arn:
        raise ValueError(
            f"device ARN {arn!r} does not match the approved ARN for {key!r}: {expected_arn!r}"
        )

    raw = response.get("deviceCapabilities")
    capabilities = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(capabilities, dict):
        raise ValueError("deviceCapabilities must be a JSON object or a JSON-encoded string")
    if "braketSchemaHeader" not in capabilities or "paradigm" not in capabilities:
        raise ValueError("deviceCapabilities is missing braketSchemaHeader or paradigm")

    service = capabilities.get("service") or {}
    when = fetched_at or datetime.now(UTC)
    return DeviceSnapshot(
        key=key,
        arn=expected_arn,
        name=str(response.get("deviceName", QPU_CANDIDATES[key].name)),
        provider=str(response.get("providerName", "")),
        device_type=str(response.get("deviceType", "")),
        status_at_fetch=str(response.get("deviceStatus", "")),
        fetched_at=when.isoformat(),
        calibration_updated_at=service.get("updatedAt"),
        capabilities_sha256=capabilities_hash(capabilities),
        capabilities=capabilities,
    )


def snapshot_path(key: str, directory: Path = DEFAULT_SNAPSHOT_DIR) -> Path:
    """Return the file that holds the snapshot for ``key``."""
    return directory / f"{key}.json"


def save_snapshot(snapshot: DeviceSnapshot, directory: Path = DEFAULT_SNAPSHOT_DIR) -> Path:
    """Write the snapshot as pretty-printed JSON and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(snapshot.key, directory)
    path.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def load_snapshot(key: str, directory: Path = DEFAULT_SNAPSHOT_DIR) -> DeviceSnapshot:
    """Load a snapshot and verify that its payload still matches the recorded hash.

    Raises:
        FileNotFoundError: If no snapshot exists for the key.
        ValueError: If the payload was edited after the hash was recorded.
    """
    path = snapshot_path(key, directory)
    if not path.is_file():
        raise FileNotFoundError(
            f"no snapshot for {key!r} at {path}; "
            "run `make device-snapshot` with read-only AWS access"
        )
    snapshot = DeviceSnapshot.from_dict(json.loads(path.read_text("utf-8")))
    actual = capabilities_hash(snapshot.capabilities)
    if actual != snapshot.capabilities_sha256:
        raise ValueError(
            f"snapshot {path} is inconsistent: recorded {snapshot.capabilities_sha256}, "
            f"payload hashes to {actual}"
        )
    return snapshot


def available_snapshots(directory: Path = DEFAULT_SNAPSHOT_DIR) -> list[str]:
    """List the approved device keys that have a snapshot on disk."""
    return [key for key in QPU_CANDIDATES if snapshot_path(key, directory).is_file()]
