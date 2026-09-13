# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

import json

import numpy as np
import pytest
from braket.circuits import Circuit
from typer.testing import CliRunner

from shor_braket.cli import app
from shor_braket.runner.local import run_local


def test_bell_probabilities():
    circuit = Circuit().h(0).cnot(0, 1).probability()

    result = run_local(circuit, shots=0)

    np.testing.assert_allclose(result.values[0], [0.5, 0.0, 0.0, 0.5], atol=1e-12)


def test_negative_shots_are_rejected():
    with pytest.raises(ValueError, match="shots must be non-negative"):
        run_local(Circuit().h(0), shots=-1)


def test_smoke_cli_samples_bell_state():
    result = CliRunner().invoke(app, ["smoke", "--shots", "128"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["backend"] == "braket_sv"
    assert report["circuit"] == "bell"
    assert report["shots"] == 128
    assert sum(report["measurement_counts"].values()) == 128
    assert set(report["measurement_counts"]) <= {"00", "11"}


@pytest.mark.parametrize("shots", ["0", "-1"])
def test_smoke_cli_rejects_nonpositive_shots(shots):
    result = CliRunner().invoke(app, ["smoke", "--shots", shots])

    assert result.exit_code == 2
