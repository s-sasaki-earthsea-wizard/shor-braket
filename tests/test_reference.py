# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

import json

import numpy as np
from typer.testing import CliRunner

from shor_braket.analysis.distribution import expected_joint_probabilities
from shor_braket.cli import app
from shor_braket.quantum.reference import build_reference_circuit
from shor_braket.runner.local import run_local
from shor_braket.runner.reference import run_reference_simulation


def test_reference_circuit_has_local_only_execution_class():
    reference = build_reference_circuit(modulus=15, base=7, count_qubit_count=8)

    assert reference.execution_class == "local-reference"
    assert reference.qpu_eligible is False
    assert len(reference.count_qubits) == 8
    assert len(reference.work_qubits) == 4
    assert reference.circuit.qubit_count == 12


def test_n15_joint_distribution_matches_the_period_four_support():
    reference = build_reference_circuit(modulus=15, base=7, count_qubit_count=4)
    circuit = reference.circuit.copy().probability(
        target=[*reference.count_qubits, *reference.work_qubits]
    )

    result = run_local(circuit, shots=0)
    probabilities = np.asarray(result.values[0])
    expected = expected_joint_probabilities(
        modulus=15,
        base=7,
        count_qubit_count=4,
        work_qubit_count=4,
    )

    np.testing.assert_allclose(probabilities, expected, atol=1e-12)
    states = {
        format(int(index), "08b")
        for index in np.flatnonzero(probabilities > 1e-12)
    }
    assert states == {
        f"{count:04b}{work:04b}"
        for count in (0, 4, 8, 12)
        for work in (1, 4, 7, 13)
    }


def test_reference_run_factors_fifteen_and_writes_artifact(tmp_path):
    report = run_reference_simulation(
        modulus=15,
        base=7,
        count_qubit_count=4,
        shots=64,
        output_dir=tmp_path,
    )

    assert report["problem"]["factorization"] == [3, 5]
    assert report["validation"]["passed"] is True
    assert report["validation"]["distribution"] == "count-work-joint"
    assert report["circuit"]["qpu_eligible"] is False
    assert report["execution"]["estimated_cost_usd"] == "0.00"
    assert sum(report["sampled"]["measurement_counts"].values()) == 64

    artifact_path = tmp_path / str(report["artifact_path"]).split(str(tmp_path) + "/", 1)[1]
    assert json.loads(artifact_path.read_text())["problem"]["factorization"] == [3, 5]


def test_simulate_cli_reports_factorization_without_aws(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "simulate",
            "--modulus",
            "15",
            "--base",
            "7",
            "--count-qubits",
            "4",
            "--shots",
            "32",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["problem"]["factorization"] == [3, 5]
    assert report["execution"]["aws_requests"] is False


def test_n6_degenerate_case_validates_without_claiming_factorization():
    report = run_reference_simulation(
        modulus=6,
        base=5,
        count_qubit_count=1,
        shots=32,
        output_dir=None,
    )

    assert report["validation"]["passed"] is True
    assert report["validation"]["factoring_succeeded"] is False
    assert report["problem"]["factorization"] is None
    assert report["problem"]["quantum_contributed"] is False
