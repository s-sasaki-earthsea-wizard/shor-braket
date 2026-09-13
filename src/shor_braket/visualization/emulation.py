# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Figures and a Markdown summary for local emulator compatibility reports."""

from __future__ import annotations

import re
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import networkx as nx
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

from shor_braket.visualization.style import (
    AMBER,
    BLUE,
    GREEN,
    INK,
    MUTED,
    PURPLE,
    RED,
    draw_arrow,
    draw_box,
    save_figure,
    style_figure,
)

DEVICE_COLORS = {"garnet": BLUE, "emerald": GREEN, "ibex": PURPLE}
COMPARED_RUNS = ("native-bell-best-edge", "native-ghz3-best-path")


def _device(report: Mapping[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], report["device"])


def _label(report: Mapping[str, Any]) -> str:
    return str(_device(report)["name"])


def _color(report: Mapping[str, Any]) -> str:
    return DEVICE_COLORS.get(str(_device(report)["key"]), INK)


def plot_execution_stages(output_dir: Path) -> dict[str, str]:
    """Static diagram of the promotion path and where AWS resources become necessary."""
    figure, ax = plt.subplots(figsize=(16, 5.6))
    style_figure(figure)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 5.2)
    ax.axis("off")
    ax.set_title(
        "Promotion path: everything left of the red line runs offline and free",
        loc="left",
        fontsize=20,
        fontweight="bold",
        color=INK,
        pad=14,
    )
    boxes = [
        (
            "1  LocalSimulator",
            "braket_sv, exact reference\ndense-matrix oracle\nno credentials, no cost",
            BLUE,
        ),
        (
            "2  LocalEmulator",
            "verbatim native gates\ncalibration noise on braket_dm\nno credentials at run time",
            BLUE,
        ),
        (
            "3  SV1 / DM1",
            "managed simulators\n$0.075 per minute\nneeds IAM + S3 (issue #3)",
            AMBER,
        ),
        (
            "4  QPU",
            "Garnet / Emerald / IBEX Q1\n$0.30 per task + per shot\n"
            "validated record\n+ Spending Limit",
            RED,
        ),
    ]
    width, gap = 3.2, 0.8
    for index, (heading, detail, color) in enumerate(boxes):
        x = 0.2 + index * (width + gap)
        draw_box(ax, x, 2.0, width, 2.0, f"{heading}\n\n{detail}", color=color, fontsize=10.5)
        if index < len(boxes) - 1:
            draw_arrow(ax, (x + width + 0.05, 3.0), (x + width + gap - 0.05, 3.0))
    boundary = 0.2 + 2 * (width + gap) - gap / 2
    ax.axvline(boundary, ymin=0.08, ymax=0.84, color=RED, linestyle="--", linewidth=2)
    ax.text(
        boundary,
        4.62,
        "AWS resources required beyond this line",
        ha="center",
        color=RED,
        fontsize=11,
        fontweight="bold",
    )
    ax.text(
        boundary - 0.15,
        1.25,
        "this report stops here",
        ha="right",
        color=RED,
        fontsize=10,
    )
    ax.text(
        0.2,
        0.55,
        "Stage 2 needs one read-only GetDevice call per device to fetch calibration JSON. "
        "The snapshot is committed, so re-runs are fully offline.",
        color=MUTED,
        fontsize=10,
    )
    return save_figure(figure, output_dir, "01_execution_stages")


def _spread_norm(values: Sequence[float], minimum_span: float = 0.01) -> Normalize:
    """Colour normalisation that stays sensible when every value is identical."""
    low, high = min(values), max(values)
    if high - low < minimum_span:
        low = high - minimum_span
    return Normalize(vmin=low, vmax=high)


def plot_topology(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Coupling graph coloured by calibration, with the selected couplers highlighted."""
    device = _device(report)
    calibration = device["calibration"]
    selected = device["selected"]
    graph = nx.Graph()
    graph.add_nodes_from(calibration["qubit_labels"])
    graph.add_edges_from(tuple(edge) for edge in calibration["edges"])

    readout = {int(q): v["readout_fidelity"] for q, v in calibration["one_qubit"].items()}
    two_qubit = {
        tuple(int(part) for part in key.split("-")): v["fidelity"]
        for key, v in calibration["two_qubit"].items()
    }

    def edge_fidelity(edge: tuple[int, int]) -> float:
        return float(two_qubit.get(edge, two_qubit.get((edge[1], edge[0]), 0.0)))

    fully_connected = bool(calibration["fully_connected"])
    layout = nx.circular_layout(graph) if fully_connected else nx.kamada_kawai_layout(graph)

    figure, ax = plt.subplots(figsize=(11, 8.5) if not fully_connected else (9, 8.5))
    style_figure(figure)
    ax.axis("off")
    node_norm = _spread_norm(list(readout.values()))
    edge_values = [edge_fidelity(edge) for edge in graph.edges]
    edge_norm = _spread_norm(edge_values)
    uniform = bool(calibration["two_qubit_uniform"])
    node_cmap = plt.get_cmap("YlGn")
    edge_cmap = plt.get_cmap("Blues")

    nx.draw_networkx_edges(
        graph,
        layout,
        ax=ax,
        edge_color=[edge_cmap(0.35 + 0.65 * edge_norm(v)) for v in edge_values],
        width=2.4 if not fully_connected else 1.2,
    )
    best = tuple(selected["best_edge"])
    worst = tuple(selected["worst_edge"])
    if not uniform and best != worst:
        nx.draw_networkx_edges(graph, layout, ax=ax, edgelist=[best], edge_color=GREEN, width=6)
        nx.draw_networkx_edges(graph, layout, ax=ax, edgelist=[worst], edge_color=RED, width=6)
    path = list(selected["best_path_3"])
    nx.draw_networkx_edges(
        graph,
        layout,
        ax=ax,
        edgelist=list(zip(path, path[1:], strict=False)),
        edge_color=PURPLE,
        width=3,
        style="dashed",
    )
    nx.draw_networkx_nodes(
        graph,
        layout,
        ax=ax,
        node_color=[node_cmap(0.25 + 0.75 * node_norm(readout[q])) for q in graph.nodes],
        node_size=520,
        edgecolors=INK,
        linewidths=0.8,
    )
    nx.draw_networkx_labels(graph, layout, ax=ax, font_size=9, font_color=INK)

    edge_bar = figure.colorbar(
        ScalarMappable(norm=edge_norm, cmap=edge_cmap),
        ax=ax,
        fraction=0.035,
        pad=0.02,
        shrink=0.8,
    )
    edge_bar.ax.set_title("2q gate\n(edge)", fontsize=8.5, color=INK)
    node_bar = figure.colorbar(
        ScalarMappable(norm=node_norm, cmap=node_cmap),
        ax=ax,
        fraction=0.035,
        pad=0.1,
        shrink=0.8,
    )
    node_bar.ax.set_title("readout\n(node)", fontsize=8.5, color=INK)

    stats = calibration["statistics"]
    two_qubit_stats = stats["two_qubit_gate_fidelity"]
    title = (
        f"{device['name']} — {calibration['qubit_count']} qubits, "
        f"{len(calibration['edges'])} couplers, native {', '.join(calibration['native_gate_set'])}"
    )
    ax.set_title(title, loc="left", fontsize=15, fontweight="bold", color=INK, pad=12)
    notes = [
        f"calibration updated {calibration['calibration_updated_at']}",
        f"best coupler {best[0]}-{best[1]} (green) = {edge_fidelity(best):.4f}, "
        f"worst {worst[0]}-{worst[1]} (red) = {edge_fidelity(worst):.4f}, "
        f"median {two_qubit_stats['median']:.4f}",
        f"GHZ path {'-'.join(str(q) for q in path)} (purple dashed)",
    ]
    if uniform:
        notes[1] = (
            f"device-level two-qubit fidelity {two_qubit_stats['median']:.4f} applied to every pair"
        )
    ax.text(
        0.0,
        -0.02,
        "\n".join(notes),
        transform=ax.transAxes,
        va="top",
        color=MUTED,
        fontsize=9.5,
    )
    return save_figure(figure, output_dir, f"02_topology_{device['key']}")


def plot_calibration(reports: Sequence[Mapping[str, Any]], output_dir: Path) -> dict[str, str]:
    """Error rates behind the noise model, per device, on a log scale."""
    figure, axes = plt.subplots(1, 3, figsize=(16, 5.4), sharey=False)
    style_figure(figure)
    panels = [
        ("one_qubit", "rb_fidelity", "1-qubit gate error  (1 - RB fidelity)"),
        ("one_qubit", "readout_fidelity", "readout error  (1 - readout fidelity)"),
        ("two_qubit", "fidelity", "2-qubit gate error  (1 - gate fidelity)"),
    ]
    rng = np.random.default_rng(7)
    for ax, (section, key, title) in zip(axes, panels, strict=False):
        for index, report in enumerate(reports):
            calibration = _device(report)["calibration"]
            values = np.array(
                [1.0 - float(row[key]) for row in calibration[section].values()], dtype=float
            )
            values = np.clip(values, 1e-5, None)
            jitter = rng.uniform(-0.18, 0.18, size=values.size)
            ax.scatter(index + jitter, values, s=22, color=_color(report), alpha=0.55, linewidths=0)
            median = float(np.median(values))
            ax.hlines(median, index - 0.3, index + 0.3, color=_color(report), linewidth=2.5)
            ax.text(
                index,
                median * 1.35,
                f"{median:.2%}",
                ha="center",
                fontsize=9,
                color=INK,
                fontweight="bold",
            )
        ax.set_yscale("log")
        ax.set_xticks(range(len(reports)))
        ax.set_xticklabels([_label(report) for report in reports], fontsize=10)
        ax.set_title(title, loc="left", fontsize=12, color=INK)
        ax.grid(axis="y", alpha=0.25)
        ax.set_facecolor("white")
    axes[0].set_ylabel("error rate (log scale)")
    figure.suptitle(
        "Calibration numbers the emulator turns into noise (dots: qubits or couplers, bar: median)",
        x=0.01,
        ha="left",
        fontsize=15,
        fontweight="bold",
        color=INK,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.93))
    return save_figure(figure, output_dir, "03_calibration_fidelities")


def plot_noise_model(reports: Sequence[Mapping[str, Any]], output_dir: Path) -> dict[str, str]:
    """How the SDK maps calibration fidelities to noise channels."""
    figure, ax = plt.subplots(figsize=(16, 7.4))
    style_figure(figure)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 7.2)
    ax.axis("off")
    ax.set_title(
        "LocalEmulator noise model: three channel types, all derived from the snapshot",
        loc="left",
        fontsize=18,
        fontweight="bold",
        color=INK,
        pad=12,
    )
    draw_box(
        ax, 0.2, 3.2, 3.0, 1.9, "Calibration snapshot\n\nGetDevice JSON\n(committed)", color=INK
    )
    rows = [
        ("1-qubit RB fidelity  f", "Depolarizing\np = (1 - f) x 3/2\non every 1-qubit gate", BLUE),
        ("readout fidelity  f", "BitFlip\np = 1 - f\nat measurement", AMBER),
        (
            "2-qubit gate fidelity  f",
            "TwoQubitDepolarizing\np = (1 - f) x 5/4\non every native 2-qubit gate",
            PURPLE,
        ),
    ]
    for index, (source, channel, color) in enumerate(rows):
        y = 5.3 - index * 1.9
        draw_arrow(ax, (3.25, 4.15), (4.35, y + 0.75))
        draw_box(ax, 4.4, y, 3.3, 1.5, source, color=color, fontsize=10.5)
        draw_arrow(ax, (7.75, y + 0.75), (8.35, y + 0.75))
        draw_box(ax, 8.4, y, 3.9, 1.5, channel, color=color, fontsize=10)
        draw_arrow(ax, (12.35, y + 0.75), (13.05, 4.15))
    draw_box(
        ax,
        13.1,
        3.2,
        2.7,
        1.9,
        "Density-matrix\nsimulation\n(braket_dm)",
        color=GREEN,
        fontsize=10.5,
    )
    lines = []
    for report in reports:
        channels = _device(report)["noise_model"]["channels"]
        total = sum(channels.values())
        lines.append(
            f"{_label(report)}: {total} channels  ("
            + ", ".join(f"{name} {count}" for name, count in channels.items())
            + ")"
        )
    lines.append(
        "Not modelled: T1/T2 decay, crosstalk, leakage, gate duration, idle errors. "
        "Verbatim circuits are not transpiled."
    )
    ax.text(0.2, 0.15, "\n".join(lines), color=MUTED, fontsize=9.5, va="bottom")
    return save_figure(figure, output_dir, "04_noise_model")


_REASON_PATTERNS = (
    (re.compile(r"must have a verbatim box"), "no verbatim box"),
    (re.compile(r"Gate (\w+) is not a native gate"), "gate {0} is not native"),
    (re.compile(r"Qubit\((\d+)\) does not exist"), "qubit {0} not on device"),
    (re.compile(r"(\d+) is not connected to qubit (\d+)"), "{0}-{1} not connected"),
)


def compact_reason(message: str) -> str:
    """Shorten a validator message to a phrase that fits a table cell."""
    for pattern, template in _REASON_PATTERNS:
        match = pattern.search(message)
        if match:
            return template.format(*match.groups())
    return textwrap.shorten(message, width=34, placeholder="…")


def plot_validation_matrix(
    reports: Sequence[Mapping[str, Any]], output_dir: Path
) -> dict[str, str]:
    """Which circuits each device emulator accepts, and the validator that rejected the rest."""
    names = [row["name"] for row in reports[0]["validation"]]
    descriptions = {row["name"]: row["description"] for row in reports[0]["validation"]}
    row_height = 0.95
    left_width = 6.4
    cell_width = 3.1
    height = 1.4 + row_height * len(names)
    width = left_width + cell_width * len(reports) + 0.4
    figure, ax = plt.subplots(figsize=(width, height))
    style_figure(figure)
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")
    ax.set_title(
        "Verbatim validation: what each emulator accepts before any noise is applied",
        loc="left",
        fontsize=16,
        fontweight="bold",
        color=INK,
        pad=10,
    )
    top = height - 0.9
    ax.text(0.2, top + 0.25, "circuit", color=MUTED, fontsize=10, fontweight="bold")
    for column, report in enumerate(reports):
        x = left_width + column * cell_width
        ax.text(
            x + cell_width / 2,
            top + 0.25,
            _label(report),
            ha="center",
            color=_color(report),
            fontsize=11,
            fontweight="bold",
        )
    for row_index, name in enumerate(names):
        y = top - (row_index + 1) * row_height
        description = textwrap.shorten(descriptions[name], width=78, placeholder="…")
        ax.text(0.2, y + 0.55, name, color=INK, fontsize=10, fontweight="bold", va="center")
        ax.text(0.2, y + 0.2, description, color=MUTED, fontsize=8.2, va="center")
        for column, report in enumerate(reports):
            outcome = next(row for row in report["validation"] if row["name"] == name)
            x = left_width + column * cell_width + 0.15
            if outcome["accepted"]:
                text = "accepted"
                color = GREEN
            else:
                text = f"rejected: {compact_reason(outcome['message'])}"
                color = RED
            draw_box(
                ax, x, y + 0.08, cell_width - 0.3, row_height - 0.16, text, color=color, fontsize=9
            )
    return save_figure(figure, output_dir, "05_validation_matrix")


def plot_noisy_vs_ideal(reports: Sequence[Mapping[str, Any]], output_dir: Path) -> dict[str, str]:
    """Ideal versus emulated distributions for the accepted smoke circuits."""
    figure, axes = plt.subplots(
        len(COMPARED_RUNS),
        len(reports),
        figsize=(5.2 * len(reports), 3.9 * len(COMPARED_RUNS)),
        squeeze=False,
    )
    style_figure(figure)
    for row_index, run_name in enumerate(COMPARED_RUNS):
        for column, report in enumerate(reports):
            ax = axes[row_index][column]
            run = report["runs"][run_name]
            width = len(run["qubits"])
            states = [format(i, f"0{width}b") for i in range(1 << width)]
            shots = run["shots"]
            ideal = [run["ideal"].get(state, 0.0) for state in states]
            emulated = [run["measurement_counts"].get(state, 0) / shots for state in states]
            positions = np.arange(len(states))
            ax.bar(positions - 0.2, ideal, width=0.4, color=BLUE, alpha=0.35, label="ideal")
            ax.bar(positions + 0.2, emulated, width=0.4, color=_color(report), label="emulated")
            ax.set_xticks(positions)
            ax.set_xticklabels(states, fontsize=8 if width > 2 else 10)
            ax.set_ylim(0, 0.62)
            ax.set_facecolor("white")
            ax.grid(axis="y", alpha=0.25)
            qubits = "-".join(str(q) for q in run["qubits"])
            ax.set_title(
                f"{_label(report)} · {run_name.replace('native-', '')} · qubits {qubits}",
                loc="left",
                fontsize=10.5,
                color=INK,
            )
            ax.text(
                0.98,
                0.95,
                f"TVD {run['tvd']:.3f}\nideal-support mass {run['ideal_support_mass']:.3f}\n"
                f"{run['two_qubit_gates']} two-qubit gates, {shots} shots",
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=8.5,
                color=INK,
                bbox={
                    "boxstyle": "round",
                    "facecolor": "white",
                    "edgecolor": MUTED,
                    "alpha": 0.9,
                },
            )
            if row_index == 0 and column == 0:
                ax.legend(loc="upper left", fontsize=8.5, frameon=False)
    figure.suptitle(
        "Calibration noise on the accepted circuits (ideal from braket_sv, emulated on braket_dm)",
        x=0.01,
        ha="left",
        fontsize=14,
        fontweight="bold",
        color=INK,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.95))
    return save_figure(figure, output_dir, "06_noisy_vs_ideal")


def plot_depth_sweep(reports: Sequence[Mapping[str, Any]], output_dir: Path) -> dict[str, str]:
    """Ideal-support mass of a Bell pair versus the number of two-qubit gates."""
    figure, ax = plt.subplots(figsize=(15, 6.4))
    style_figure(figure)
    ax.set_facecolor("white")
    x_max = 1
    for report in reports:
        sweep = report["depth_sweep"]
        color = _color(report)
        for label, style in (("best", "-"), ("worst", "--")):
            edge = sweep["edges"][label]
            x = [point["two_qubit_gates"] for point in edge["points"]]
            y = [point["ideal_support_mass"] for point in edge["points"]]
            qubits = "-".join(str(q) for q in edge["qubits"])
            ax.plot(
                x,
                y,
                style,
                marker="o",
                color=color,
                linewidth=2,
                label=(
                    f"{_label(report)} {label} coupler {qubits} "
                    f"(f2q = {edge['two_qubit_fidelity']:.4f}, "
                    f"f1q = {edge['one_qubit_rb_min']:.4f})"
                ),
            )
        best = sweep["edges"]["best"]
        fidelity = float(best["two_qubit_fidelity"])
        x_max = max(x_max, max(p["two_qubit_gates"] for p in best["points"]))
        grid = np.linspace(0, x_max, 200)
        ax.plot(
            grid,
            0.5 + 0.5 * fidelity**grid,
            ":",
            color=color,
            linewidth=1.4,
            alpha=0.8,
        )
    ax.axhline(0.5, color=MUTED, linewidth=1, linestyle="-.")
    ax.text(0, 0.505, "0.5 = fully depolarised pair", color=MUTED, fontsize=9, va="bottom")
    ax.set_xlabel("two-qubit gates in the verbatim circuit (Bell pair + identity CNOT pairs)")
    ax.set_ylabel("probability mass on the ideal support {00, 11}")
    ax.set_ylim(0.45, 1.02)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=9, loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False)
    ax.set_title(
        "Depth budget: how fast calibration noise erodes a Bell pair\n"
        "(dotted: 0.5 + 0.5·f2q^n from the two-qubit fidelity alone; "
        "solid: best coupler, dashed: worst coupler)",
        loc="left",
        fontsize=12.5,
        fontweight="bold",
        color=INK,
        pad=12,
    )
    return save_figure(figure, output_dir, "07_depth_sweep")


def generate_emulation_figures(
    reports: Sequence[Mapping[str, Any]], artifact_dir: Path
) -> dict[str, Any]:
    """Render every figure for one or more device reports into ``artifact_dir/figures``."""
    figure_dir = artifact_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    figures: dict[str, dict[str, str]] = {"execution_stages": plot_execution_stages(figure_dir)}
    for report in reports:
        figures[f"topology_{_device(report)['key']}"] = plot_topology(report, figure_dir)
    figures["calibration_fidelities"] = plot_calibration(reports, figure_dir)
    figures["noise_model"] = plot_noise_model(reports, figure_dir)
    figures["validation_matrix"] = plot_validation_matrix(reports, figure_dir)
    figures["noisy_vs_ideal"] = plot_noisy_vs_ideal(reports, figure_dir)
    figures["depth_sweep"] = plot_depth_sweep(reports, figure_dir)
    return {"generator": "matplotlib", "figures": figures}


def emulation_markdown(reports: Sequence[Mapping[str, Any]]) -> str:
    """Markdown tables summarising the reports, for the Wiki and the artifact directory."""
    lines = ["# LocalEmulator 互換性レポート", ""]
    lines.append("## デバイス")
    lines.append("")
    lines.append(
        "| デバイス | qubit | カプラ | ネイティブゲート | 校正更新 | 1q RB 中央値 | "
        "読み出し中央値 | 2q 中央値 | ノイズチャネル |"
    )
    lines.append("|---|---:|---:|---|---|---:|---:|---:|---:|")
    for report in reports:
        device = _device(report)
        calibration = device["calibration"]
        stats = calibration["statistics"]
        channels = sum(device["noise_model"]["channels"].values())
        lines.append(
            f"| {device['name']} | {calibration['qubit_count']} | {len(calibration['edges'])} | "
            f"`{' '.join(calibration['native_gate_set'])}` | "
            f"{calibration['calibration_updated_at']} | "
            f"{stats['one_qubit_rb_fidelity']['median']:.5f} | "
            f"{stats['readout_fidelity']['median']:.5f} | "
            f"{stats['two_qubit_gate_fidelity']['median']:.5f} | {channels} |"
        )
    lines.append("")
    lines.append("## 検証行列")
    lines.append("")
    header = "| 回路 | " + " | ".join(_label(report) for report in reports) + " |"
    lines.append(header)
    lines.append("|---|" + "---|" * len(reports))
    for row in reports[0]["validation"]:
        cells = []
        for report in reports:
            outcome = next(r for r in report["validation"] if r["name"] == row["name"])
            if outcome["accepted"]:
                cells.append("✅ accepted")
            else:
                cells.append(f"❌ {outcome['error_type']}: {outcome['message'][:90]}")
        lines.append(f"| `{row['name']}` | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## ノイズあり実行")
    lines.append("")
    lines.append("| デバイス | 回路 | qubit | 2q ゲート | shots | TVD | 理想サポート上の確率 |")
    lines.append("|---|---|---|---:|---:|---:|---:|")
    for report in reports:
        for name, run in report["runs"].items():
            qubits = "-".join(str(q) for q in run["qubits"])
            lines.append(
                f"| {_label(report)} | `{name}` | {qubits} | {run['two_qubit_gates']} | "
                f"{run['shots']} | {run['tvd']:.3f} | {run['ideal_support_mass']:.3f} |"
            )
    lines.append("")
    lines.append("## 深さスイープ（Bell + 恒等 CNOT 対）")
    lines.append("")
    sweep_points = reports[0]["depth_sweep"]["edges"]["best"]["points"]
    lines.append(
        "| デバイス | カプラ | 2q 忠実度 | 1q RB (最小) | "
        + " | ".join(f"n={p['two_qubit_gates']}" for p in sweep_points)
        + " |"
    )
    lines.append("|---|---|---:|---:|" + "---:|" * len(sweep_points))
    for report in reports:
        for label in ("best", "worst"):
            edge = report["depth_sweep"]["edges"][label]
            qubits = "-".join(str(q) for q in edge["qubits"])
            values = " | ".join(f"{p['ideal_support_mass']:.3f}" for p in edge["points"])
            lines.append(
                f"| {_label(report)} | {label} {qubits} | {edge['two_qubit_fidelity']:.4f} | "
                f"{edge['one_qubit_rb_min']:.4f} | {values} |"
            )
    lines.append("")
    return "\n".join(lines)
