# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shor_braket.cli import app
from shor_braket.devices import summarize_snapshot
from shor_braket.quantum.native import bell_circuit, gate_family, verbatim
from shor_braket.quantum.reference import build_reference_circuit
from shor_braket.runner.emulator import (
    build_emulator,
    run_emulator_report,
    run_native_circuit,
    validate_circuit,
    validation_matrix,
)


def test_reference_circuit_is_rejected_by_the_emulator(garnet_snapshot):
    emulator = build_emulator(garnet_snapshot)
    reference = build_reference_circuit(modulus=15, base=7, count_qubit_count=8).circuit

    accepted, error_type, message = validate_circuit(emulator, reference)
    assert accepted is False
    assert "verbatim box" in message

    accepted, error_type, message = validate_circuit(emulator, verbatim(reference))
    assert accepted is False
    assert "not a native gate" in message


def test_native_bell_on_a_real_coupler_is_accepted_and_noisy(garnet_snapshot):
    summary = summarize_snapshot(garnet_snapshot)
    family = gate_family(summary.native_gate_set)
    emulator = build_emulator(garnet_snapshot)
    edge = summary.best_edge()

    run = run_native_circuit(emulator, bell_circuit(edge, family), list(edge), shots=400)

    assert run["measured_qubits"] == sorted(edge)
    assert set(run["ideal"]) == {"00", "11"}
    assert run["two_qubit_gates"] == 1
    assert sum(run["measurement_counts"].values()) == 400
    assert 0.0 < run["tvd"] < 0.3
    assert 0.7 < run["ideal_support_mass"] < 1.0


def test_validation_matrix_only_accepts_verbatim_native_circuits(garnet_snapshot):
    summary = summarize_snapshot(garnet_snapshot)
    emulator = build_emulator(garnet_snapshot)

    outcomes = {row.name: row for row in validation_matrix(emulator, summary, "prx-cz")}

    accepted = {name for name, row in outcomes.items() if row.accepted}
    assert accepted == {"native-bell-best-edge", "native-ghz3-best-path"}
    assert outcomes["native-bell-no-verbatim"].message.startswith("The input circuit must have")
    assert "not connected" in outcomes["native-bell-non-adjacent"].message
    assert outcomes["textbook-bell-verbatim"].error_type == "EmulatorValidationError"


def test_all_to_all_device_accepts_distant_pairs(ibex_snapshot):
    summary = summarize_snapshot(ibex_snapshot)
    emulator = build_emulator(ibex_snapshot)

    outcomes = {row.name: row for row in validation_matrix(emulator, summary, "prx-xx")}

    assert outcomes["native-bell-non-adjacent"].accepted is True
    assert outcomes["native-bell-unknown-qubit"].accepted is False


def test_report_records_snapshot_and_refuses_qpu_eligibility(
    garnet_snapshot, snapshot_dir, tmp_path
):
    report = run_emulator_report(
        device_key="garnet",
        shots=200,
        sweep_shots=200,
        output_dir=tmp_path,
        snapshot_dir=snapshot_dir,
        visualize=False,
    )

    assert report["execution"]["class"] == "local-emulator"
    assert report["execution"]["aws_requests"] is False
    assert report["qpu_gate"]["qpu_eligible"] is False
    assert report["qpu_gate"]["validated_record_issued"] is False
    expected_hash = garnet_snapshot.capabilities_sha256
    assert report["device"]["snapshot"]["capabilities_sha256"] == expected_hash
    assert report["device"]["noise_model"]["channels"]["TwoQubitDepolarizing"] == len(
        report["device"]["calibration"]["edges"]
    )
    points = report["depth_sweep"]["edges"]["best"]["points"]
    assert [p["two_qubit_gates"] for p in points] == [1, 5, 11, 21, 41, 81]
    artifact = Path(report["artifact_path"])
    assert artifact.name == "result.json"
    assert json.loads(artifact.read_text())["device"]["key"] == "garnet"
    assert not (tmp_path / "validated").exists()


def test_emulate_cli_rejects_managed_simulators(snapshot_dir):
    result = CliRunner().invoke(
        app, ["emulate", "--device", "sv1", "--snapshot-dir", str(snapshot_dir)]
    )

    assert result.exit_code == 1
    assert "no local emulator target" in result.output


def test_snapshot_import_cli_validates_the_arn(tmp_path):
    payload = json.dumps(
        {
            "deviceArn": "arn:aws:braket:us-east-1::device/qpu/ionq/Forte-1",
            "deviceCapabilities": json.dumps({"braketSchemaHeader": {}, "paradigm": {}}),
        }
    )
    result = CliRunner().invoke(
        app,
        ["snapshot-import", "--device", "garnet", "--snapshot-dir", str(tmp_path)],
        input=payload,
    )

    assert result.exit_code == 1
    assert "does not match the approved ARN" in result.output
    assert not (tmp_path / "garnet.json").exists()


@pytest.mark.slow
def test_emulate_cli_writes_figures_for_one_device(snapshot_dir, tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "emulate",
            "--device",
            "garnet",
            "--shots",
            "100",
            "--sweep-shots",
            "100",
            "--output-dir",
            str(tmp_path),
            "--snapshot-dir",
            str(snapshot_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    report = json.loads(Path(summary["artifact_path"]).read_text())
    figures = report["visualizations"]["figures"]
    expected = {"execution_stages", "topology_garnet", "validation_matrix", "depth_sweep"}
    assert expected <= set(figures)
    for formats in figures.values():
        assert (Path(summary["artifact_path"]).parent / formats["png"]).stat().st_size > 0
