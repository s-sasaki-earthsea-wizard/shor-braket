# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Command-line entry points for local development."""

import json
from pathlib import Path
from typing import Annotated

import typer
from braket.circuits import Circuit

from shor_braket.cost import qpu_cost_estimates
from shor_braket.runner.local import run_local
from shor_braket.runner.reference import run_reference_simulation

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


@app.command("simulate")
def simulate(
    modulus: Annotated[int, typer.Option("--modulus", "-n", min=3)] = 15,
    base: Annotated[int, typer.Option("--base", "-a", min=2)] = 7,
    count_qubits: Annotated[int, typer.Option("--count-qubits", "-t", min=1)] = 8,
    shots: Annotated[int, typer.Option(min=1)] = 1000,
    output_dir: Annotated[Path, typer.Option(file_okay=False)] = Path("runs/raw"),
    visualize: Annotated[
        bool,
        typer.Option("--visualize/--no-visualize", help="Generate educational SVG/PNG reports."),
    ] = True,
) -> None:
    """Factor an integer with the local-only Shor reference circuit."""
    report = run_reference_simulation(
        modulus=modulus,
        base=base,
        count_qubit_count=count_qubits,
        shots=shots,
        output_dir=output_dir,
        visualize=visualize,
    )
    typer.echo(json.dumps(report, indent=2, sort_keys=True))
    if not report["validation"]["passed"]:
        raise typer.Exit(code=1)


@app.command("qpu-costs")
def qpu_costs(shots: Annotated[int, typer.Option(min=1)] = 1000) -> None:
    """Estimate one-task costs for the three approved future QPUs."""
    typer.echo(
        json.dumps(
            {
                "shots": shots,
                "price_checked_at": "2026-09-14",
                "source": "https://aws.amazon.com/braket/pricing/",
                "estimates": qpu_cost_estimates(shots),
            },
            indent=2,
            sort_keys=True,
        )
    )
