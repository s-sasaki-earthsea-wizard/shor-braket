# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Command-line entry points for local development."""

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer
from botocore.exceptions import BotoCoreError, ClientError
from braket.circuits import Circuit
from braket.circuits.serialization import IRType

from shor_braket.aws_session import (
    AwsConfigurationError,
    caller_identity,
    readonly_session,
    results_bucket,
    submission_session,
)
from shor_braket.cost import QPU_CANDIDATES, qpu_cost_estimates
from shor_braket.devices import (
    DEFAULT_SNAPSHOT_DIR,
    load_snapshot,
    save_snapshot,
    snapshot_from_get_device,
    summarize_snapshot,
)
from shor_braket.gate.circuit_hash import CANONICAL_FORM_VERSION, circuit_hash
from shor_braket.gate.preflight import SPENDING_LIMIT_CHECKS
from shor_braket.gate.record import (
    DEFAULT_RECORD_DIR,
    DEFAULT_VALIDITY_DAYS,
    iter_records,
    record_path,
)
from shor_braket.gate.spending import (
    SpendingLimitLookup,
    aws_spending_limit_lookup,
    no_spending_limit_lookup,
)
from shor_braket.quantum.n15 import ORACLE_MODES
from shor_braket.runner.decoherence import decoherence_study
from shor_braket.runner.emulator import run_emulator_comparison, run_emulator_report
from shor_braket.runner.local import run_local
from shor_braket.runner.n15 import run_n15_emulation
from shor_braket.runner.n15_iterative import DEFAULT_SWEEP_SHOTS, run_iterative_emulation
from shor_braket.runner.reference import run_reference_simulation
from shor_braket.runner.report import run_report
from shor_braket.runner.submit import (
    DEFAULT_RUN_DIR,
    build_submission_circuit,
    plan_submission,
    submit,
)
from shor_braket.runner.task_status import task_status_report

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


def _echo_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


@app.command("snapshot-import")
def snapshot_import(
    device: Annotated[str, typer.Option("--device", help="garnet | emerald | ibex")],
    snapshot_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SNAPSHOT_DIR,
    input_path: Annotated[
        Path | None,
        typer.Option("--input", dir_okay=False, help="GetDevice JSON; default reads stdin."),
    ] = None,
) -> None:
    """Store a `braket get-device` response as an offline snapshot for the emulator."""
    text = input_path.read_text("utf-8") if input_path else sys.stdin.read()
    try:
        snapshot = snapshot_from_get_device(json.loads(text), key=device)
    except ValueError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    path = save_snapshot(snapshot, snapshot_dir)
    _echo_json(
        {
            "path": str(path),
            "device_arn": snapshot.arn,
            "device_name": snapshot.name,
            "status_at_fetch": snapshot.status_at_fetch,
            "fetched_at": snapshot.fetched_at,
            "calibration_updated_at": snapshot.calibration_updated_at,
            "capabilities_sha256": snapshot.capabilities_sha256,
        }
    )


@app.command("snapshot-show")
def snapshot_show(
    device: Annotated[str, typer.Option("--device", help="garnet | emerald | ibex")],
    snapshot_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SNAPSHOT_DIR,
) -> None:
    """Summarise a committed snapshot: qubits, native gates, couplers, fidelities, price."""
    if device not in QPU_CANDIDATES:
        typer.echo(
            f"error: {device!r} is not an approved QPU; choose one of {sorted(QPU_CANDIDATES)}",
            err=True,
        )
        raise typer.Exit(code=1)
    snapshot = load_snapshot(device, snapshot_dir)
    summary = summarize_snapshot(snapshot)
    _echo_json(
        {
            "device": {
                "key": device,
                "name": snapshot.name,
                "arn": snapshot.arn,
                "status_at_fetch": snapshot.status_at_fetch,
                "fetched_at": snapshot.fetched_at,
                "calibration_updated_at": snapshot.calibration_updated_at,
                "capabilities_sha256": snapshot.capabilities_sha256,
            },
            "qubit_count": summary.qubit_count,
            "native_gate_set": list(summary.native_gate_set),
            "fully_connected": summary.fully_connected,
            "couplers": len(summary.edges),
            "best_edge": list(summary.best_edge()),
            "worst_edge": list(summary.worst_edge()),
            "statistics": summary.statistics(),
            "shots_range": list(summary.shots_range) if summary.shots_range else None,
            "price_per_shot_usd": summary.price_per_shot_usd,
            "execution_windows": summary.execution_windows,
        }
    )


@app.command("emulate")
def emulate(
    device: Annotated[str, typer.Option("--device", help="garnet | emerald | ibex | all")] = "all",
    shots: Annotated[int, typer.Option(min=1)] = 2000,
    sweep_shots: Annotated[int, typer.Option(min=1)] = 4000,
    output_dir: Annotated[Path, typer.Option(file_okay=False)] = Path("runs/raw"),
    snapshot_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SNAPSHOT_DIR,
    visualize: Annotated[
        bool,
        typer.Option("--visualize/--no-visualize", help="Render figures and a Markdown report."),
    ] = True,
) -> None:
    """Validate and run native smoke circuits on calibration-backed local emulators (offline)."""
    keys = list(QPU_CANDIDATES) if device == "all" else [device]
    unknown = [key for key in keys if key not in QPU_CANDIDATES]
    if unknown:
        typer.echo(
            f"error: {unknown[0]!r} has no local emulator target; "
            f"choose one of {sorted(QPU_CANDIDATES)} or 'all'",
            err=True,
        )
        raise typer.Exit(code=1)
    if device == "all":
        comparison = run_emulator_comparison(
            keys,
            shots=shots,
            sweep_shots=sweep_shots,
            output_dir=output_dir,
            snapshot_dir=snapshot_dir,
            visualize=visualize,
        )
        comparison.pop("reports")
        _echo_json(comparison)
        return
    report = run_emulator_report(
        device_key=device,
        shots=shots,
        sweep_shots=sweep_shots,
        output_dir=output_dir,
        snapshot_dir=snapshot_dir,
        visualize=visualize,
    )
    _echo_json(
        {
            "artifact_path": report.get("artifact_path"),
            "device": report["device"]["key"],
            "calibration_updated_at": report["device"]["snapshot"]["calibration_updated_at"],
            "accepted": [row["name"] for row in report["validation"] if row["accepted"]],
            "rejected": [row["name"] for row in report["validation"] if not row["accepted"]],
            "runs": {
                name: {"tvd": run["tvd"], "ideal_support_mass": run["ideal_support_mass"]}
                for name, run in report["runs"].items()
            },
            "qpu_gate": report["qpu_gate"],
        }
    )


@app.command("emulate-n15")
def emulate_n15(
    device: Annotated[str, typer.Option("--device", help="garnet | emerald | ibex | all")] = "all",
    oracle: Annotated[
        str, typer.Option("--oracle", help="generic-constant | generic-repeated | all")
    ] = "all",
    shots: Annotated[int, typer.Option(min=1)] = 20_000,
    count_qubits: Annotated[int, typer.Option("--count-qubits", "-t", min=1)] = 2,
    output_dir: Annotated[Path, typer.Option(file_okay=False)] = Path("runs/raw"),
    snapshot_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SNAPSHOT_DIR,
    visualize: Annotated[
        bool,
        typer.Option("--visualize/--no-visualize", help="Render figures and a Markdown report."),
    ] = True,
    issue_records: Annotated[
        bool,
        typer.Option(
            "--issue-records/--no-issue-records",
            help="Write a validated record for every configuration that passes.",
        ),
    ] = True,
    record_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_RECORD_DIR,
    validity_days: Annotated[int, typer.Option(min=1)] = DEFAULT_VALIDITY_DAYS,
) -> None:
    """Emulate the N = 15 swap-network circuit on calibration-backed local emulators (offline)."""
    keys = list(QPU_CANDIDATES) if device == "all" else [device]
    modes = list(ORACLE_MODES) if oracle == "all" else [oracle]
    if any(key not in QPU_CANDIDATES for key in keys):
        typer.echo(
            f"error: {device!r} has no local emulator target; "
            f"choose one of {sorted(QPU_CANDIDATES)} or 'all'",
            err=True,
        )
        raise typer.Exit(code=1)
    if any(mode not in ORACLE_MODES for mode in modes):
        typer.echo(
            f"error: {oracle!r} is not an oracle mode; choose one of {list(ORACLE_MODES)} or 'all'",
            err=True,
        )
        raise typer.Exit(code=1)
    report = run_n15_emulation(
        device_keys=keys,
        oracle_modes=modes,
        shots=shots,
        count_qubit_count=count_qubits,
        output_dir=output_dir,
        snapshot_dir=snapshot_dir,
        visualize=visualize,
        issue_records=issue_records,
        record_dir=record_dir,
        validity_days=validity_days,
    )
    _echo_json(
        {
            "artifact_path": report.get("artifact_path"),
            "claim": report["problem"]["claim"],
            "configurations": {
                key: {
                    "accepted": config["validation"]["accepted"],
                    "native_two_qubit": config["gates"]["native_two_qubit"],
                    "swaps": config["layout"]["swap_count"],
                    "exact_tvd": config.get("metrics", {}).get("exact_tvd"),
                    "signal_fraction": config.get("metrics", {}).get("signal_fraction_exact"),
                }
                for key, config in report["configurations"].items()
            },
            "qpu_gate": report["qpu_gate"],
        }
    )


@app.command("emulate-n15-iterative")
def emulate_n15_iterative(
    device: Annotated[str, typer.Option("--device", help="garnet | emerald | ibex | all")] = "all",
    oracle: Annotated[
        str, typer.Option("--oracle", help="generic-constant | generic-repeated | all")
    ] = "all",
    shots: Annotated[int, typer.Option(min=1, help="Shots of the sampled runs.")] = 4000,
    count_qubits: Annotated[int, typer.Option("--count-qubits", "-t", min=1)] = 2,
    sweep_shots: Annotated[
        str, typer.Option("--sweep-shots", help="Comma-separated shot counts for the Monte Carlo.")
    ] = ",".join(str(n) for n in DEFAULT_SWEEP_SHOTS),
    repetitions: Annotated[int, typer.Option(min=2)] = 200,
    standard: Annotated[
        bool,
        typer.Option("--standard/--no-standard", help="Also run the standard circuit."),
    ] = True,
    output_dir: Annotated[Path, typer.Option(file_okay=False)] = Path("runs/raw"),
    snapshot_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SNAPSHOT_DIR,
    visualize: Annotated[
        bool,
        typer.Option("--visualize/--no-visualize", help="Render figures and a Markdown report."),
    ] = True,
) -> None:
    """Emulate the iterative (feed-forward) N = 15 circuit next to the standard one (offline).

    Comparison axis only: the standard circuit stays the default (see quantum/n15_iterative.py).
    """
    keys = list(QPU_CANDIDATES) if device == "all" else [device]
    modes = list(ORACLE_MODES) if oracle == "all" else [oracle]
    if any(key not in QPU_CANDIDATES for key in keys):
        typer.echo(
            f"error: {device!r} has no local emulator target; "
            f"choose one of {sorted(QPU_CANDIDATES)} or 'all'",
            err=True,
        )
        raise typer.Exit(code=1)
    if any(mode not in ORACLE_MODES for mode in modes):
        typer.echo(
            f"error: {oracle!r} is not an oracle mode; choose one of {list(ORACLE_MODES)} or 'all'",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        sweep = [int(part) for part in sweep_shots.split(",") if part.strip()]
    except ValueError:
        typer.echo("error: --sweep-shots must be comma-separated integers", err=True)
        raise typer.Exit(code=1) from None
    report = run_iterative_emulation(
        device_keys=keys,
        oracle_modes=modes,
        shots=shots,
        count_qubit_count=count_qubits,
        sweep_shots=sweep,
        repetitions=repetitions,
        include_standard=standard,
        output_dir=output_dir,
        snapshot_dir=snapshot_dir,
        visualize=visualize,
    )
    _echo_json(
        {
            "artifact_path": report.get("artifact_path"),
            "claim": report["problem"]["claim"],
            "configurations": {
                key: {
                    "accepted": config["validation"]["accepted"],
                    "native_two_qubit": config.get("gates", {}).get("native_two_qubit"),
                    "swaps": config.get("layout", {}).get("swap_count"),
                    "error_budget": config.get("layout", {}).get("error_budget"),
                    "exact_tvd": config.get("metrics", {}).get("exact_tvd"),
                    "signal_fraction": config.get("metrics", {}).get("signal_fraction_exact"),
                    "support_signal_fraction": config.get("metrics", {}).get(
                        "support_signal_fraction_exact"
                    ),
                    "predicted_signal_fraction": config.get("metrics", {}).get(
                        "predicted_signal_fraction"
                    ),
                }
                for key, config in report["configurations"].items()
            },
            "qpu_gate": report["qpu_gate"],
        }
    )


@app.command("circuit")
def circuit(
    device: Annotated[str, typer.Option("--device", help="garnet | emerald | ibex")],
    oracle: Annotated[
        str, typer.Option("--oracle", help="generic-constant | generic-repeated")
    ] = "generic-repeated",
    count_qubits: Annotated[int, typer.Option("--count-qubits", "-t", min=1)] = 2,
    snapshot_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SNAPSHOT_DIR,
    show_qasm: Annotated[
        bool, typer.Option("--qasm/--no-qasm", help="Print the OpenQASM source as well.")
    ] = False,
) -> None:
    """Build the verbatim program for one device and print its hash; nothing is executed."""
    try:
        program = build_submission_circuit(
            device_key=device,
            oracle_mode=oracle,
            count_qubit_count=count_qubits,
            snapshot_dir=snapshot_dir,
        )
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error

    payload: dict[str, Any] = {
        "device": device,
        "oracle_mode": oracle,
        "count_qubits": count_qubits,
        "circuit_hash": circuit_hash(program),
        "canonical_form_version": CANONICAL_FORM_VERSION,
        "instruction_count": len(program.instructions),
        "qubits": sorted(int(q) for q in program.qubits),
        "has_validated_record": record_path(circuit_hash(program)).is_file(),
    }
    if show_qasm:
        payload["openqasm"] = str(program.to_ir(ir_type=IRType.OPENQASM).source)
    _echo_json(payload)


@app.command("records")
def records(
    record_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_RECORD_DIR,
    snapshot_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SNAPSHOT_DIR,
) -> None:
    """List the validated records on disk and say which ones the preflight would still accept."""
    # The expiry date is the weaker of the two freshness tests. What actually retires a record
    # is the device being recalibrated, which can happen the day after it was issued, so a
    # listing that only showed `expired` would show stale records as healthy.
    snapshots: dict[str, str | None] = {}

    def calibration_hash(device_key: str) -> str | None:
        """Return the capability hash of the snapshot on disk for one device."""
        if device_key not in snapshots:
            try:
                snapshots[device_key] = load_snapshot(device_key, snapshot_dir).capabilities_sha256
            except (FileNotFoundError, ValueError):
                snapshots[device_key] = None
        return snapshots[device_key]

    rows = []
    for record in iter_records(record_dir):
        current = calibration_hash(record.device_key)
        recorded = record.snapshot.get("capabilities_sha256")
        calibration_current = None if current is None else current == recorded
        expired = record.is_expired()
        rows.append(
            {
                "circuit_hash": record.circuit_hash,
                "device": record.device_key,
                "oracle_mode": record.oracle_mode,
                "issued_at": record.issued_at,
                "expires_at": record.expires_at,
                "expired": expired,
                "signal_fraction": record.emulation.get("verdict", {}).get(
                    "signal_fraction_exact"
                ),
                "calibration_updated_at": record.snapshot.get("calibration_updated_at"),
                "calibration_current": calibration_current,
                "usable": calibration_current is True and not expired,
            }
        )
    usable = sum(1 for row in rows if row["usable"])
    _echo_json(
        {
            "record_dir": str(record_dir),
            "snapshot_dir": str(snapshot_dir),
            "count": len(rows),
            "usable": usable,
            "records": rows,
        }
    )


@app.command("submit-qpu")
def submit_qpu(
    device: Annotated[str, typer.Option("--device", help="garnet | emerald | ibex")],
    shots: Annotated[int, typer.Option(min=1)],
    oracle: Annotated[
        str, typer.Option("--oracle", help="generic-constant | generic-repeated")
    ] = "generic-repeated",
    count_qubits: Annotated[int, typer.Option("--count-qubits", "-t", min=1)] = 2,
    snapshot_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SNAPSHOT_DIR,
    record_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_RECORD_DIR,
    max_cost: Annotated[
        str | None, typer.Option("--max-cost", help="Per-task ceiling in USD.")
    ] = None,
    campaign: Annotated[
        str | None,
        typer.Option(
            "--campaign",
            envvar="BRAKET_CAMPAIGN",
            help="Cost-grouping tag. Grants no access; AQT needs its own role.",
        ),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the prompt. Requires --max-cost.")
    ] = False,
    execute: Annotated[
        bool,
        typer.Option(
            "--execute/--no-execute",
            help="Create a real, paid quantum task. Without it this is a free dry run.",
        ),
    ] = False,
    run_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_RUN_DIR,
) -> None:
    """Run the submission gate, and with --execute create the paid task behind it."""
    if yes and max_cost is None:
        typer.echo("error: --yes requires an explicit --max-cost", err=True)
        raise typer.Exit(code=2)

    # Without --execute nothing here may reach AWS, so the spending limit stays unreadable and
    # the gate closes on it. That is the dry run: it proves everything except the money.
    spending_lookup: SpendingLimitLookup = no_spending_limit_lookup
    session = None
    caller: dict[str, str] = {}
    bucket = ""
    if execute:
        try:
            session = submission_session(device)
            caller = caller_identity(session)
            bucket = results_bucket()
        except AwsConfigurationError as error:
            typer.echo(f"error: {error}", err=True)
            raise typer.Exit(code=2) from error
        except (ClientError, BotoCoreError) as error:
            typer.echo(f"error: could not authenticate the submission profile: {error}", err=True)
            raise typer.Exit(code=2) from error
        typer.echo(f"[gate] submitting as            {caller['arn']}")
        spending_lookup = aws_spending_limit_lookup(session.client("braket"))

    try:
        plan = plan_submission(
            device_key=device,
            oracle_mode=oracle,
            shots=shots,
            count_qubit_count=count_qubits,
            snapshot_dir=snapshot_dir,
            record_dir=record_dir,
            max_cost_usd=Decimal(max_cost) if max_cost is not None else None,
            spending_lookup=spending_lookup,
            campaign=campaign,
        )
    except (ValueError, ArithmeticError, FileNotFoundError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    except (ClientError, BotoCoreError) as error:
        typer.echo(f"error: could not read the spending limit: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(plan.report.render())
    if not execute:
        # A dry run has no credentials, so the spending-limit checks cannot pass and would make
        # every dry run a failure. Report on the checks it could actually answer, and say plainly
        # that the money checks are still ahead. The real path below has no such exemption.
        offline_blockers = [
            check for check in plan.report.blockers if check.name not in SPENDING_LIMIT_CHECKS
        ]
        if offline_blockers:
            names = ", ".join(check.name for check in offline_blockers)
            typer.echo(f"\nrefused: offline checks failed ({names}).", err=True)
            raise typer.Exit(code=1)
        typer.echo("\ndry run: every check that works without credentials passed.")
        typer.echo("The spending limit was not read here and is still ahead of any real task.")
        typer.echo("Run make submit-qpu to read it and create the paid task.")
        return
    if not plan.allowed:
        typer.echo("\nrefused: the preflight did not pass; nothing was submitted.", err=True)
        raise typer.Exit(code=1)
    if not yes and not typer.confirm(
        f"\nCreate a paid task on {plan.report.device_name} "
        f"for about {plan.report.cost['estimated_cost_usd']} USD?",
        default=False,
    ):
        typer.echo("cancelled.")
        raise typer.Exit(code=1)

    assert session is not None  # noqa: S101 - narrowed by the execute branch above
    try:
        result = submit(plan, session=session, bucket=bucket, run_dir=run_dir, caller=caller)
    except (PermissionError, ValueError) as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error
    except (ClientError, BotoCoreError) as error:
        typer.echo(f"error: the service refused the task: {error}", err=True)
        raise typer.Exit(code=1) from error
    _echo_json(result.to_dict())


@app.command("task-status")
def task_status(
    task_arn: Annotated[
        str | None,
        typer.Option("--task-arn", help="One task to ask about. Default: every recorded task."),
    ] = None,
    run_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_RUN_DIR,
) -> None:
    """Report the state of the quantum tasks this repository created (read-only, free)."""
    try:
        session = readonly_session()
        _echo_json(task_status_report(session, task_arn=task_arn, run_dir=run_dir))
    except AwsConfigurationError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error
    except (ClientError, BotoCoreError) as error:
        typer.echo(f"error: could not read the task: {error}", err=True)
        raise typer.Exit(code=1) from error


@app.command("report")
def report(
    task_arn: Annotated[
        str | None,
        typer.Option("--task-arn", help="One task to analyse. Default: every recorded task."),
    ] = None,
    result_file: Annotated[
        Path | None,
        typer.Option(
            "--result-file",
            dir_okay=False,
            help="Read results.json from disk instead of S3 (offline, free).",
        ),
    ] = None,
    run_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_RUN_DIR,
) -> None:
    """Compare a finished task with the ideal distribution and with the emulator's prediction."""
    try:
        session = None if result_file is not None else readonly_session()
        _echo_json(run_report(session, task_arn=task_arn, result_file=result_file, run_dir=run_dir))
    except AwsConfigurationError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error
    except (ValueError, KeyError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    except (ClientError, BotoCoreError) as error:
        typer.echo(f"error: could not read the result: {error}", err=True)
        raise typer.Exit(code=1) from error


@app.command("decoherence-study")
def decoherence(
    device: Annotated[str, typer.Option("--device", help="garnet | emerald")] = "garnet",
    oracle: Annotated[
        str, typer.Option("--oracle", help="generic-constant | generic-repeated")
    ] = "generic-constant",
    count_qubits: Annotated[int, typer.Option("--count-qubits", "-t", min=1)] = 2,
    snapshot_dir: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SNAPSHOT_DIR,
) -> None:
    """Predict hardware metrics with idle T1/T2 decay and asymmetric readout added (offline)."""
    try:
        _echo_json(
            decoherence_study(
                device_key=device,
                oracle_mode=oracle,
                count_qubit_count=count_qubits,
                snapshot_dir=snapshot_dir,
            )
        )
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
