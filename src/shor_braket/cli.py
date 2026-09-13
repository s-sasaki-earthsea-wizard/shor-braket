# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Command-line entry points for local development."""

import json
from typing import Annotated

import typer
from braket.circuits import Circuit

from shor_braket.runner.local import run_local

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Develop and check circuits with the local Braket simulator."""


@app.command()
def smoke(shots: Annotated[int, typer.Option(min=1)] = 1000) -> None:
    """Run a two-qubit Bell circuit to check the local environment."""
    circuit = Circuit().h(0).cnot(0, 1)
    result = run_local(circuit, shots=shots)
    typer.echo(
        json.dumps(
            {
                "backend": "braket_sv",
                "circuit": "bell",
                "shots": shots,
                "measurement_counts": dict(result.measurement_counts),
            },
            indent=2,
            sort_keys=True,
        )
    )
