# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Figures and Markdown for the N = 15 emulation report."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from matplotlib import pyplot as plt

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
MODE_LABELS = {"generic-constant": "generic-constant", "generic-repeated": "generic-repeated"}
MODE_HATCH = {"generic-constant": "", "generic-repeated": "//"}


def _devices(report: Mapping[str, Any]) -> list[str]:
    return list(report["devices"])


def _modes(report: Mapping[str, Any]) -> list[str]:
    modes: list[str] = []
    for key in report["configurations"]:
        mode = key.split("/", 1)[1]
        if mode not in modes:
            modes.append(mode)
    return modes


def _config(report: Mapping[str, Any], device: str, mode: str) -> dict[str, Any]:
    return dict(report["configurations"][f"{device}/{mode}"])


def _name(report: Mapping[str, Any], device: str) -> str:
    return str(report["devices"][device]["name"])


def plot_logical_circuit(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Schematic of the t = 2 QPE with the N = 15 swap-network oracle."""
    figure, ax = plt.subplots(figsize=(16, 6.2))
    style_figure(figure)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 6)
    ax.axis("off")
    ax.set_title(
        "N = 15, a = 7, t = 2: the QPU-oriented circuit on 6 qubits (generic-constant oracle)",
        loc="left",
        fontsize=17,
        fontweight="bold",
        color=INK,
        pad=12,
    )
    rows = {"c1": 5.0, "c0": 4.1, "w3": 3.0, "w2": 2.3, "w1": 1.6, "w0": 0.9}
    for label, y in rows.items():
        ax.plot([1.2, 15.6], [y, y], color=MUTED, linewidth=1.2, zorder=1)
        ax.text(0.9, y, label, ha="right", va="center", fontsize=11, color=INK)
    ax.text(0.9, 5.55, "count", ha="right", va="center", fontsize=9, color=MUTED)
    ax.text(0.9, 3.5, "work", ha="right", va="center", fontsize=9, color=MUTED)
    draw_box(ax, 1.5, 4.75, 0.7, 0.5, "H", color=BLUE, fontsize=10)
    draw_box(ax, 1.5, 3.85, 0.7, 0.5, "H", color=BLUE, fontsize=10)
    draw_box(ax, 1.5, 0.65, 0.7, 0.5, "X", color=AMBER, fontsize=10)
    draw_box(
        ax,
        3.0,
        0.6,
        3.6,
        2.8,
        "controlled x4 mod 15\n\nrotate 4 bits by 2\n= 2 Fredkins (16 CNOT)\n"
        "control: c1  (a^2 = 4)",
        color=PURPLE,
        fontsize=10,
    )
    ax.plot([4.8, 4.8], [3.4, 5.0], color=PURPLE, linewidth=2)
    ax.plot(4.8, 5.0, "o", color=PURPLE, markersize=9)
    draw_box(
        ax,
        7.4,
        0.6,
        3.6,
        2.8,
        "controlled x7 mod 15\n\nrotate by 3, then NOT all\n= 3 Fredkins + 4 CNOT (28)\n"
        "control: c0  (a^1 = 7)",
        color=PURPLE,
        fontsize=10,
    )
    ax.plot([9.2, 9.2], [3.4, 4.1], color=PURPLE, linewidth=2)
    ax.plot(9.2, 4.1, "o", color=PURPLE, markersize=9)
    draw_box(ax, 11.8, 4.75, 0.7, 0.5, "H", color=BLUE, fontsize=10)
    draw_box(ax, 12.9, 3.6, 1.3, 1.9, "CPhase\n-pi/2\n(2 CNOT)", color=BLUE, fontsize=9)
    draw_box(ax, 14.6, 3.85, 0.7, 0.5, "H", color=BLUE, fontsize=10)
    ax.text(
        12.0, 5.7, "inverse QFT (no final swap: read c0 as the MSB of y)", color=MUTED, fontsize=9.5
    )
    constant = _config(report, _devices(report)[0], "generic-constant")
    ax.text(
        1.2,
        0.15,
        f"logical two-qubit gates: {constant['gates']['logical_two_qubit']}  "
        "(generic-repeated applies x7 twice instead of x4: 86).  "
        "Fredkin = CNOT · Toffoli(6 CNOT) · CNOT.  Everything below CNOT is native-lowered later.",
        color=MUTED,
        fontsize=9.5,
    )
    return save_figure(figure, output_dir, "01_logical_circuit")


def plot_layout(report: Mapping[str, Any], device: str, output_dir: Path) -> dict[str, str]:
    """Device graph with the six chosen physical qubits and their register roles."""
    calibration = report["devices"][device]["calibration"]
    graph = nx.Graph()
    graph.add_nodes_from(calibration["qubit_labels"])
    graph.add_edges_from(tuple(edge) for edge in calibration["edges"])
    fully_connected = bool(calibration["fully_connected"])
    layout = nx.circular_layout(graph) if fully_connected else nx.kamada_kawai_layout(graph)
    modes = _modes(report)
    figure, axes = plt.subplots(1, len(modes), figsize=(7.5 * len(modes), 6.8), squeeze=False)
    style_figure(figure)
    for ax, mode in zip(axes[0], modes, strict=False):
        config = _config(report, device, mode)
        used = {int(k): v for k, v in config["layout"]["roles"].items()}
        transit = [int(q) for q in config["layout"].get("transit_qubits", [])]
        ax.axis("off")
        nx.draw_networkx_edges(graph, layout, ax=ax, edge_color="#C7D2FE", width=1.2)
        nx.draw_networkx_nodes(
            graph, layout, ax=ax, node_color="#E2E8F0", node_size=260, edgecolors=MUTED
        )
        if transit:
            nx.draw_networkx_nodes(
                graph,
                layout,
                ax=ax,
                nodelist=transit,
                node_color="white",
                node_size=460,
                edgecolors=MUTED,
                linewidths=2.5,
            )
        nx.draw_networkx_nodes(
            graph,
            layout,
            ax=ax,
            nodelist=list(used),
            node_color=[BLUE if role.startswith("c") else AMBER for role in used.values()],
            node_size=520,
            edgecolors=INK,
        )
        nx.draw_networkx_labels(
            graph, layout, ax=ax, labels={q: f"{q}\n{r}" for q, r in used.items()}, font_size=8
        )
        others = {q: str(q) for q in graph.nodes if q not in used}
        nx.draw_networkx_labels(graph, layout, ax=ax, labels=others, font_size=7, font_color=MUTED)
        gates = config["gates"]
        ax.set_title(
            f"{_name(report, device)} · {mode}\n"
            f"{config['layout']['swap_count']} SWAPs, {gates['native_two_qubit']} native 2q gates, "
            f"{gates['native']['prx']} prx, depth {gates['native_depth']}",
            loc="left",
            fontsize=11,
            color=INK,
        )
    figure.text(
        0.01,
        0.01,
        "blue: count qubits (c1 controls x4, c0 controls x7), amber: work qubits w3..w0 "
        "(w0 is the LSB), grey ring: SWAP transit qubits.\n"
        "Layout chosen by trying every assignment on compact neighbourhoods and keeping "
        "the smallest error budget.",
        color=MUTED,
        fontsize=9,
    )
    return save_figure(figure, output_dir, f"02_layout_{device}")


def plot_gate_budget(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Two-qubit gate counts from the logical circuit to the routed native one."""
    devices, modes = _devices(report), _modes(report)
    figure, ax = plt.subplots(figsize=(12, 5.6))
    style_figure(figure)
    ax.set_facecolor("white")
    width = 0.8 / len(modes)
    for mode_index, mode in enumerate(modes):
        for device_index, device in enumerate(devices):
            config = _config(report, device, mode)
            gates = config["gates"]
            x = device_index + (mode_index - (len(modes) - 1) / 2) * width
            logical = gates["logical_two_qubit"]
            swaps = 3 * config["layout"]["swap_count"]
            ax.bar(
                x,
                logical,
                width=width * 0.9,
                color=DEVICE_COLORS[device],
                alpha=0.85,
                hatch=MODE_HATCH[mode],
                edgecolor="white",
            )
            ax.bar(
                x,
                swaps,
                bottom=logical,
                width=width * 0.9,
                color=RED,
                alpha=0.75,
                hatch=MODE_HATCH[mode],
                edgecolor="white",
            )
            ax.text(
                x,
                logical + swaps + 2,
                f"{gates['native_two_qubit']}\n({config['layout']['swap_count']} SWAP)",
                ha="center",
                va="bottom",
                fontsize=9,
                color=INK,
            )
    ax.set_xticks(range(len(devices)))
    ax.set_xticklabels([_name(report, d) for d in devices])
    ax.set_ylabel("two-qubit native gates after routing")
    ax.set_ylim(
        0,
        max(_config(report, d, m)["gates"]["native_two_qubit"] for d in devices for m in modes)
        * 1.3,
    )
    ax.grid(axis="y", alpha=0.25)
    ax.set_title(
        "Two-qubit gate budget: logical circuit (device colour) + routing SWAPs "
        "(red, 3 gates each); "
        "hatched = generic-repeated",
        loc="left",
        fontsize=12,
        fontweight="bold",
        color=INK,
    )
    return save_figure(figure, output_dir, "03_gate_budget")


def plot_joint_distributions(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Heatmaps of the joint (count, work) distribution, ideal versus each configuration."""
    devices, modes = _devices(report), _modes(report)
    columns = 1 + len(modes)
    figure, axes = plt.subplots(
        len(devices), columns, figsize=(5.6 * columns, 3.3 * len(devices)), squeeze=False
    )
    style_figure(figure)
    first = _config(report, devices[0], modes[0])
    t = int(first["count_qubit_count"])
    expected = np.asarray(first["distributions"]["expected"]).reshape(1 << t, 16)
    vmax = float(expected.max())
    for row, device in enumerate(devices):
        panels = [("ideal (any device)", expected)]
        for mode in modes:
            config = _config(report, device, mode)
            noisy = np.asarray(config["distributions"]["exact_noisy"]).reshape(1 << t, 16)
            metrics = config["metrics"]
            panels.append(
                (
                    f"{mode}\nTVD {metrics['exact_tvd']:.3f}, "
                    f"signal {metrics['signal_fraction_exact']:.2f}, "
                    f"{config['gates']['native_two_qubit']} 2q gates",
                    noisy,
                )
            )
        for column, (title, matrix) in enumerate(panels):
            ax = axes[row][column]
            image = ax.imshow(matrix, cmap="YlGnBu", vmin=0, vmax=vmax, aspect="auto")
            ax.set_xticks(range(16))
            ax.set_xticklabels([str(v) for v in range(16)], fontsize=7)
            ax.set_yticks(range(1 << t))
            ax.set_yticklabels([str(v) for v in range(1 << t)], fontsize=8)
            ax.set_title(f"{_name(report, device)} · {title}", loc="left", fontsize=9, color=INK)
            if column == 0:
                ax.set_ylabel("count value y", fontsize=9)
            if row == len(devices) - 1:
                ax.set_xlabel("work value (orbit of 7: 1, 7, 4, 13)", fontsize=9)
            for value in (1, 4, 7, 13):
                ax.axvline(value, color=RED, alpha=0.15, linewidth=6)
    figure.subplots_adjust(hspace=0.6, wspace=0.18)
    figure.colorbar(image, ax=axes.ravel().tolist(), fraction=0.02, pad=0.01, label="probability")
    figure.suptitle(
        "Joint distribution of the count register (rows) and work register (columns): "
        "ideal has 16 equal cells on the orbit, noise spreads them out",
        x=0.01,
        ha="left",
        fontsize=13,
        fontweight="bold",
        color=INK,
    )
    return save_figure(figure, output_dir, "04_joint_distributions")


def plot_signal_survival(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Signal fraction and per-shot order recovery per configuration."""
    devices, modes = _devices(report), _modes(report)
    figure, (left, right) = plt.subplots(1, 2, figsize=(15, 5.6))
    style_figure(figure)
    width = 0.8 / len(modes)
    for ax in (left, right):
        ax.set_facecolor("white")
        ax.grid(axis="y", alpha=0.25)
        ax.set_xticks(range(len(devices)))
        ax.set_xticklabels([_name(report, d) for d in devices])
    for mode_index, mode in enumerate(modes):
        for device_index, device in enumerate(devices):
            config = _config(report, device, mode)
            metrics = config["metrics"]
            x = device_index + (mode_index - (len(modes) - 1) / 2) * width
            color = DEVICE_COLORS[device]
            left.bar(
                x,
                metrics["signal_fraction_exact"],
                width=width * 0.9,
                color=color,
                hatch=MODE_HATCH[mode],
                edgecolor="white",
                alpha=0.9,
            )
            left.text(
                x,
                metrics["signal_fraction_exact"] + 0.02,
                f"{metrics['signal_fraction_exact']:.2f}\nTVD {metrics['exact_tvd']:.3f}\n"
                f"{config['gates']['native_two_qubit']} 2q",
                ha="center",
                va="bottom",
                fontsize=8.5,
                color=INK,
            )
            right.bar(
                x,
                metrics["order_recovery_rate"],
                width=width * 0.9,
                color=color,
                hatch=MODE_HATCH[mode],
                edgecolor="white",
                alpha=0.9,
            )
            right.text(
                x,
                metrics["order_recovery_rate"] + 0.01,
                f"{metrics['order_recovery_rate']:.2f}",
                ha="center",
                va="bottom",
                fontsize=9,
                color=INK,
            )
    left.set_ylim(0, 1.15)
    left.set_ylabel("signal fraction  1 - TVD / TVD(ideal, uniform)")
    left.set_title(
        "How much of the period-4 structure survives (exact noisy distribution)",
        loc="left",
        fontsize=11,
        fontweight="bold",
        color=INK,
    )
    baseline = _config(report, devices[0], modes[0])["metrics"]["order_recovery_baseline_uniform_y"]
    right.axhline(baseline, color=RED, linestyle="--", linewidth=1.5)
    right.text(
        len(devices) - 0.55,
        1.02,
        f"dashed: uniformly random y already recovers r = 4 in {baseline:.0%} of shots",
        ha="right",
        color=RED,
        fontsize=9,
    )
    right.set_ylim(0, 1.15)
    right.set_ylabel("per-shot order recovery rate (exact noisy distribution)")
    right.set_title(
        "Why 'we found r = 4' proves nothing at t = 2",
        loc="left",
        fontsize=11,
        fontweight="bold",
        color=INK,
    )
    figure.text(
        0.01,
        0.01,
        "hatched bars: generic-repeated oracle (x7 applied twice instead of x4)",
        color=MUTED,
        fontsize=9,
    )
    figure.tight_layout(rect=(0, 0.03, 1, 1))
    return save_figure(figure, output_dir, "05_signal_survival")


def plot_gates_vs_signal(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Signal fraction against routed two-qubit gate count, one point per configuration."""
    devices, modes = _devices(report), _modes(report)
    figure, ax = plt.subplots(figsize=(11, 5.8))
    style_figure(figure)
    ax.set_facecolor("white")
    for device in devices:
        xs, ys = [], []
        for mode in modes:
            config = _config(report, device, mode)
            x = config["gates"]["native_two_qubit"]
            y = config["metrics"]["signal_fraction_exact"]
            xs.append(x)
            ys.append(y)
            ax.scatter(
                x,
                y,
                s=110 if mode == "generic-constant" else 80,
                color=DEVICE_COLORS[device],
                marker="o" if mode == "generic-constant" else "s",
                zorder=3,
                edgecolors=INK,
            )
            ax.annotate(
                f"{_name(report, device)}\n{mode}",
                (x, y),
                textcoords="offset points",
                xytext=(8, -4),
                fontsize=8,
                color=INK,
            )
        ax.plot(xs, ys, color=DEVICE_COLORS[device], alpha=0.5, linewidth=1.5)
    ax.set_xlabel("native two-qubit gates after routing")
    ax.set_ylabel("signal fraction")
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.25)
    ax.set_title(
        "More structure exploited -> fewer gates -> more signal "
        "(circles: generic-constant, squares: generic-repeated)",
        loc="left",
        fontsize=11.5,
        fontweight="bold",
        color=INK,
    )
    return save_figure(figure, output_dir, "06_gates_vs_signal")


def plot_budget_vs_signal(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Signal fraction against the summed calibration error budget, with exp(-budget)."""
    devices, modes = _devices(report), _modes(report)
    figure, ax = plt.subplots(figsize=(11, 5.8))
    style_figure(figure)
    ax.set_facecolor("white")
    budgets: list[float] = []
    for device in devices:
        for mode in modes:
            config = _config(report, device, mode)
            budget = float(config["layout"]["error_budget"])
            signal = float(config["metrics"]["signal_fraction_exact"])
            offset = (8, 6) if len(budgets) % 2 == 0 else (8, -16)
            budgets.append(budget)
            ax.scatter(
                budget,
                signal,
                s=110 if mode == "generic-constant" else 80,
                color=DEVICE_COLORS[device],
                marker="o" if mode == "generic-constant" else "s",
                zorder=3,
                edgecolors=INK,
            )
            ax.annotate(
                f"{_name(report, device)}\n{mode}",
                (budget, signal),
                textcoords="offset points",
                xytext=offset,
                fontsize=8,
                color=INK,
            )
    grid = np.linspace(0, max(budgets) * 1.15, 200)
    ax.plot(grid, np.exp(-grid), ":", color=MUTED, linewidth=1.6, label="exp(-budget)")
    ax.set_xlabel(
        "error budget = sum over gates of (1 - fidelity) + readout errors of the register"
    )
    ax.set_ylabel("signal fraction")
    ax.set_ylim(0, 1.05)
    ax.set_xlim(0, max(budgets) * 1.15)
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)
    ax.set_title(
        "A one-number predictor: the summed calibration error budget of the routed circuit",
        loc="left",
        fontsize=11.5,
        fontweight="bold",
        color=INK,
    )
    return save_figure(figure, output_dir, "07_budget_vs_signal")


def plot_pipeline(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Static diagram of the compilation pipeline used for every configuration."""
    figure, ax = plt.subplots(figsize=(16, 4.6))
    style_figure(figure)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 4.4)
    ax.axis("off")
    ax.set_title(
        "From the logical circuit to the emulator: "
        "what happens to the N = 15 circuit before it runs",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color=INK,
        pad=10,
    )
    boxes = [
        ("1  logical", "h, x, t, rz, cnot\n46 / 86 two-qubit gates", BLUE),
        ("2  place + route", "compact neighbourhood search\ngreedy SWAP insertion", AMBER),
        ("3  lower to native", "prx / cz  or  prx / xx / rz\nvirtual-Z phase tracking", PURPLE),
        (
            "4  verbatim box",
            "no transpilation by the SDK\nvalidators: gates, qubits, couplers",
            INK,
        ),
        ("5  LocalEmulator", "braket_dm + calibration noise\nexact + sampled distributions", GREEN),
    ]
    width, gap = 2.75, 0.45
    for index, (heading, detail, color) in enumerate(boxes):
        x = 0.2 + index * (width + gap)
        draw_box(ax, x, 1.2, width, 2.1, f"{heading}\n\n{detail}", color=color, fontsize=9.5)
        if index < len(boxes) - 1:
            draw_arrow(ax, (x + width + 0.03, 2.25), (x + width + gap - 0.03, 2.25))
    ax.text(
        0.2,
        0.45,
        "Correctness is checked at every stage against the ideal joint distribution "
        "(TVD ~ 1e-15 on braket_sv); the emulator then adds only the calibration noise.",
        color=MUTED,
        fontsize=9.5,
    )
    return save_figure(figure, output_dir, "00_pipeline")


def generate_n15_figures(report: Mapping[str, Any], artifact_dir: Path) -> dict[str, Any]:
    """Render all figures for the N = 15 emulation report."""
    figure_dir = artifact_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    figures: dict[str, dict[str, str]] = {
        "pipeline": plot_pipeline(report, figure_dir),
        "logical_circuit": plot_logical_circuit(report, figure_dir),
    }
    for device in _devices(report):
        figures[f"layout_{device}"] = plot_layout(report, device, figure_dir)
    figures["gate_budget"] = plot_gate_budget(report, figure_dir)
    figures["joint_distributions"] = plot_joint_distributions(report, figure_dir)
    figures["signal_survival"] = plot_signal_survival(report, figure_dir)
    figures["gates_vs_signal"] = plot_gates_vs_signal(report, figure_dir)
    figures["budget_vs_signal"] = plot_budget_vs_signal(report, figure_dir)
    return {"generator": "matplotlib", "figures": figures}


def n15_markdown(report: Mapping[str, Any]) -> str:
    """Markdown tables summarising every configuration."""
    lines = ["# N = 15 QPU 互換回路の LocalEmulator 結果", ""]
    lines.append(
        f"shots = {report['execution']['shots']}、t = {report['problem']['count_qubits']}、"
        f"{report['problem']['claim']}"
    )
    lines.append("")
    lines.append(
        "| デバイス | oracle | 配置 (物理 qubit) | SWAP | 2q (論理→ネイティブ) | "
        "prx | 深さ | 検証 | TVD (exact) | TVD (sampled) | 信号残存率 | "
        "理想サポート質量 | 位数復元率 (基準 0.75) |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|")
    for key, config in report["configurations"].items():
        device, mode = key.split("/", 1)
        roles = ", ".join(f"{q}={r}" for q, r in config["layout"]["roles"].items())
        gates = config["gates"]
        if not config["validation"]["accepted"]:
            lines.append(
                f"| {_name(report, device)} | {mode} | {roles} | "
                f"{config['layout']['swap_count']} | "
                f"{gates['logical_two_qubit']}→{gates['native_two_qubit']} | "
                f"{gates['native'].get('prx', 0)} | "
                f"{gates['native_depth']} | ❌ {config['validation']['message'][:60]} | | | | | |"
            )
            continue
        m = config["metrics"]
        lines.append(
            f"| {_name(report, device)} | {mode} | {roles} | {config['layout']['swap_count']} | "
            f"{gates['logical_two_qubit']}→{gates['native_two_qubit']} | "
            f"{gates['native'].get('prx', 0)} | "
            f"{gates['native_depth']} | ✅ | {m['exact_tvd']:.3f} | {m['sampled_tvd']:.3f} | "
            f"{m['signal_fraction_exact']:.2f} | {m['ideal_support_mass_exact']:.3f} | "
            f"{m['order_recovery_rate']:.3f} |"
        )
    lines.append("")
    lines.append(
        "信号残存率は 1 − TVD / TVD(理想, 一様) で、"
        "理想分布と一様分布の混合とみなしたときの理想側の重み。"
    )
    lines.append("")
    return "\n".join(lines)
