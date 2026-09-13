# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from shor_braket.cost import QPU_CANDIDATES
from shor_braket.devices import (
    load_snapshot,
    save_snapshot,
    snapshot_from_get_device,
    summarize_snapshot,
)
from shor_braket.devices.snapshot import available_snapshots, capabilities_hash

MINIMAL_CAPABILITIES = {
    "braketSchemaHeader": {
        "name": "braket.device_schema.iqm.iqm_device_capabilities",
        "version": "1",
    },
    "paradigm": {"qubitCount": 2, "nativeGateSet": ["cz", "prx"]},
    "service": {"updatedAt": "2026-09-13T00:00:00+00:00"},
}


def _response(key: str = "garnet", arn: str | None = None) -> dict:
    return {
        "deviceArn": arn or QPU_CANDIDATES[key].arn,
        "deviceName": "Garnet",
        "providerName": "IQM",
        "deviceType": "QPU",
        "deviceStatus": "ONLINE",
        "deviceQueueInfo": [{"queue": "QUANTUM_TASKS_QUEUE", "queueSize": "3"}],
        "deviceCapabilities": json.dumps(MINIMAL_CAPABILITIES),
    }


def test_snapshot_keeps_metadata_and_drops_queue_state(tmp_path):
    snapshot = snapshot_from_get_device(_response(), key="garnet")

    assert snapshot.arn == QPU_CANDIDATES["garnet"].arn
    assert snapshot.calibration_updated_at == "2026-09-13T00:00:00+00:00"
    assert snapshot.capabilities_sha256 == capabilities_hash(MINIMAL_CAPABILITIES)
    assert "deviceQueueInfo" not in json.dumps(snapshot.to_dict())

    path = save_snapshot(snapshot, tmp_path)
    assert path == tmp_path / "garnet.json"
    assert load_snapshot("garnet", tmp_path) == snapshot
    assert available_snapshots(tmp_path) == ["garnet"]


def test_snapshot_rejects_wrong_device_or_unknown_key():
    with pytest.raises(ValueError, match="does not match the approved ARN"):
        snapshot_from_get_device(
            _response(arn="arn:aws:braket:us-east-1::device/qpu/ionq/Forte-1"), key="garnet"
        )
    with pytest.raises(ValueError, match="unknown device key"):
        snapshot_from_get_device(_response(), key="forte")


def test_edited_snapshot_fails_the_hash_check(tmp_path):
    snapshot = snapshot_from_get_device(_response(), key="garnet")
    path = save_snapshot(snapshot, tmp_path)
    data = json.loads(path.read_text())
    data["capabilities"]["paradigm"]["qubitCount"] = 99
    path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="inconsistent"):
        load_snapshot("garnet", tmp_path)


def test_missing_snapshot_points_at_the_make_target(tmp_path):
    with pytest.raises(FileNotFoundError, match="make device-snapshot"):
        load_snapshot("emerald", tmp_path)


def test_committed_snapshots_summarise_without_aws(snapshot_dir):
    keys = available_snapshots(snapshot_dir)
    if not keys:
        pytest.skip("no committed snapshots")
    for key in keys:
        summary = summarize_snapshot(load_snapshot(key, snapshot_dir))
        assert summary.qubit_count == QPU_CANDIDATES[key].qubits
        assert summary.edges
        best = summary.best_edge()
        assert summary.is_adjacent(*best)
        assert len(summary.best_path(3)) == 3
        stats = summary.statistics()
        assert 0.9 < stats["two_qubit_gate_fidelity"]["median"] <= 1.0
        assert summary.price_per_shot_usd == float(QPU_CANDIDATES[key].price_per_shot_usd)
        assert summary.fully_connected == (key == "ibex")
