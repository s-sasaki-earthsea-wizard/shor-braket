# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Figures and Markdown for the iterative-vs-standard N = 15 emulation report."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib import pyplot as plt

from shor_braket.visualization.n15 import DEVICE_COLORS
from shor_braket.visualization.style import INK, MUTED, save_figure, style_figure

METHOD_MARKERS = {"standard-qpe": "o", "iterative-qpe": "D"}
METHOD_LABELS = {"standard-qpe": "standard QPE (t count qubits)", "iterative-qpe": "iterative QPE"}


def _accepted(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: config
        for name, config in report["configurations"].items()
        if config["validation"]["accepted"]
    }


def _name(report: Mapping[str, Any], device: str) -> str:
    return str(report["devices"][device]["name"])


def plot_methods_budget_vs_signal(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Exact signal fraction against the error budget for both methods, joined per device."""
    accepted = _accepted(report)
    figure, ax = plt.subplots(figsize=(11, 5.8))
    style_figure(figure)
    ax.set_facecolor("white")
    budgets: list[float] = []
    pairs: dict[tuple[str, str], dict[str, tuple[float, float]]] = {}
    for name, config in accepted.items():
        device, mode, method = name.split("/")
        budget = float(config["layout"]["error_budget"])
        signal = float(config["metrics"]["signal_fraction_exact"])
        budgets.append(budget)
        pairs.setdefault((device, mode), {})[method] = (budget, signal)
        ax.scatter(
            budget,
            signal,
            s=110 if mode == "generic-constant" else 80,
            color=DEVICE_COLORS[device],
            marker=METHOD_MARKERS[method],
            zorder=3,
            edgecolors=INK,
            alpha=1.0 if mode == "generic-constant" else 0.6,
        )
        if method == "standard-qpe" or len(pairs[(device, mode)]) == 1:
            ax.annotate(
                f"{_name(report, device)} {mode.split('-')[1]}",
                (budget, signal),
                textcoords="offset points",
                xytext=(9, 4) if mode == "generic-constant" else (9, -12),
                fontsize=7.5,
                color=INK,
            )
    for points in pairs.values():
        if len(points) == 2:
            (x0, y0), (x1, y1) = points["standard-qpe"], points["iterative-qpe"]
            ax.annotate(
                "",
                xy=(x1, y1),
                xytext=(x0, y0),
                arrowprops={"arrowstyle": "->", "color": MUTED, "lw": 1.0},
            )
    if budgets:
        grid = np.linspace(0, max(budgets) * 1.15, 200)
        ax.plot(grid, np.exp(-grid), ":", color=MUTED, linewidth=1.6, label="exp(-budget)")
        ax.set_xlim(0, max(budgets) * 1.15)
    for method, marker in METHOD_MARKERS.items():
        ax.scatter(
            [], [], marker=marker, color="white", edgecolors=INK, label=METHOD_LABELS[method]
        )
    ax.set_xlabel("error budget = sum over routed gates of (1 - fidelity) + readout errors")
    ax.set_ylabel("signal fraction (exact, 1 - TVD / 0.75)")
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, loc="upper right")
    ax.set_title(
        "Arrows go from the standard to the iterative circuit of the same device and oracle",
        loc="left",
        fontsize=11.5,
        fontweight="bold",
        color=INK,
    )
    return save_figure(figure, output_dir, "01_methods_budget_vs_signal")


def plot_sampling_sweep(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    """Sampled TVD and the two signal-fraction estimators as functions of the shot count."""
    sampling = report["sampling"]
    shots = np.asarray(sampling["shots_list"], dtype=float)
    figure, (left, right) = plt.subplots(1, 2, figsize=(13, 5.4))
    style_figure(figure)
    for ax in (left, right):
        ax.set_facecolor("white")
        ax.set_xscale("log")
        ax.set_xlabel("shots")
        ax.grid(alpha=0.25, which="both")

    ideal = sampling["ideal"]
    left.plot(
        shots,
        [row["tvd_to_expected"]["mean"] for row in ideal["rows"]],
        color=INK,
        linewidth=2,
        label="ideal distribution (pure sampling floor)",
    )
    left.plot(
        shots,
        [row["floor_formula"] for row in ideal["rows"]],
        "--",
        color=INK,
        linewidth=1,
        label="floor formula, ideal",
    )
    for name, sweep in sampling.items():
        if name in ("shots_list", "ideal"):
            continue
        device, mode, method = name.split("/")
        color = DEVICE_COLORS[device]
        style = "-" if method == "iterative-qpe" else ":"
        alpha = 1.0 if mode == "generic-constant" else 0.5
        means = [row["tvd_to_expected"]["mean"] for row in sweep["rows"]]
        low = [row["tvd_to_expected"]["p025"] for row in sweep["rows"]]
        high = [row["tvd_to_expected"]["p975"] for row in sweep["rows"]]
        left.plot(shots, means, style, color=color, alpha=alpha, linewidth=1.8, label=f"{name}")
        left.fill_between(shots, low, high, color=color, alpha=0.08)
        left.axhline(sweep["exact_tvd"], color=color, alpha=0.35 * alpha, linewidth=0.8)
        if method == "iterative-qpe":
            right.plot(
                shots,
                [row["signal_fraction_tvd"]["mean"] for row in sweep["rows"]],
                "--",
                color=color,
                alpha=alpha,
                linewidth=1.6,
                label=f"{device} {mode.split('-')[1]}: from TVD",
            )
            right.plot(
                shots,
                [row["signal_fraction_support"]["mean"] for row in sweep["rows"]],
                "-",
                color=color,
                alpha=alpha,
                linewidth=1.8,
                label=f"{device} {mode.split('-')[1]}: from support mass",
            )
            right.axhline(
                sweep["exact_signal_fraction_tvd"], color=color, alpha=0.35 * alpha, linewidth=0.8
            )
    left.set_ylabel("TVD of the sample to the ideal joint distribution")
    left.set_ylim(0, None)
    left.legend(frameon=False, fontsize=7)
    left.set_title(
        "Sampled TVD vs shots (thin lines: exact TVD)",
        loc="left",
        fontsize=10.5,
        fontweight="bold",
        color=INK,
    )
    right.set_ylabel("signal fraction estimate")
    right.set_ylim(0, 1.05)
    right.legend(frameon=False, fontsize=7)
    right.set_title(
        "Signal-fraction estimators vs shots (iterative circuits)",
        loc="left",
        fontsize=10.5,
        fontweight="bold",
        color=INK,
    )
    return save_figure(figure, output_dir, "02_sampling_sweep")


def generate_iterative_figures(report: Mapping[str, Any], artifact_dir: Path) -> dict[str, Any]:
    """Render every figure of the report into ``artifact_dir/figures``."""
    output_dir = artifact_dir / "figures"
    output_dir.mkdir(exist_ok=True)
    figures: dict[str, Any] = {}
    if _accepted(report):
        figures["methods_budget_vs_signal"] = plot_methods_budget_vs_signal(report, output_dir)
    figures["sampling_sweep"] = plot_sampling_sweep(report, output_dir)
    return figures


def _pick_shots(shots_list: list[int]) -> list[int]:
    preferred: list[int] = [n for n in (1000, 4000, 20_000) if n in shots_list]
    if preferred:
        return preferred
    return sorted({shots_list[0], shots_list[len(shots_list) // 2], shots_list[-1]})


def iterative_markdown(report: Mapping[str, Any]) -> str:
    """Markdown tables: both methods per device, then the finite-shot behaviour."""
    lines = ["# N = 15: 反復 QPE と標準 QPE の LocalEmulator 比較", ""]
    lines.append(
        f"shots = {report['execution']['shots']}、t = {report['problem']['count_qubits']}、"
        f"{report['problem']['claim']}"
    )
    lines.append("")
    lines.append("## 1. 構成ごとの結果")
    lines.append("")
    lines.append(
        "| デバイス | oracle | 方式 | 配置 (物理 qubit) | SWAP | 2q (論理→ネイティブ) | prx | "
        "cc_prx | measure_ff | 深さ | 誤り予算 B | exp(−B) | 検証 | TVD (exact) | "
        "λ (TVD) | λ (サポート質量) | Hellinger | 位数復元率 (基準 0.75) |"
    )
    lines.append(
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|"
    )
    for name, config in report["configurations"].items():
        device, mode, method = name.split("/")
        if "layout" not in config:
            lines.append(
                f"| {_name(report, device)} | {mode} | {method} | | | | | | | | | | "
                f"❌ {config['validation']['message'][:70]} | | | | | |"
            )
            continue
        roles = ", ".join(f"{q}={r}" for q, r in config["layout"]["roles"].items())
        gates = config["gates"]
        native = gates["native"]
        budget = float(config["layout"]["error_budget"])
        head = (
            f"| {_name(report, device)} | {mode} | {method} | {roles} | "
            f"{config['layout']['swap_count']} | "
            f"{gates['logical_two_qubit']}→{gates['native_two_qubit']} | "
            f"{native.get('prx', 0)} | {native.get('cc_prx', 0)} | {native.get('measure_ff', 0)} | "
            f"{gates['native_depth']} | {budget:.3f} | {np.exp(-budget):.2f} | "
        )
        if not config["validation"]["accepted"]:
            lines.append(head + f"❌ {config['validation']['message'][:60]} | | | | | |")
            continue
        m = config["metrics"]
        lines.append(
            head + f"✅ | {m['exact_tvd']:.3f} | {m['signal_fraction_exact']:.3f} | "
            f"{m['support_signal_fraction_exact']:.3f} | {m['hellinger_fidelity_exact']:.3f} | "
            f"{m['order_recovery_rate']:.3f} |"
        )
    lines.append("")
    lines.append(
        "λ (TVD) = 1 − TVD / 0.75、λ (サポート質量) = (理想サポート上の質量 − 1/4) / (3/4)。"
        "どちらも一様混合モデルの信号残存率で、厳密分布では両者はほぼ一致する。"
        "反復 QPE の B には measure_ff の読み出し誤りと cc_prx の 1q 誤りを含む。"
    )
    lines.append("")
    diagnostics = [
        (name, config["sdk_run_diagnostics"])
        for name, config in report["configurations"].items()
        if config.get("sdk_run_diagnostics", {}).get("shots", 0) > 0
    ]
    if diagnostics:
        lines.append("### SDK の feed-forward 実行（診断のみ、指標には使わない）")
        lines.append("")
        lines.append(
            "| 構成 | shots | 厳密 (サポート質量) | 読み出し誤りのみ | braket_dm の shot 毎実行 | "
            "LocalEmulator.run |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|")
        for name, diag in diagnostics:
            exact_mass = report["configurations"][name]["metrics"]["ideal_support_mass_exact"]
            lines.append(
                f"| {name} | {diag['shots']} | {exact_mass:.3f} | "
                f"{diag['readout_only_ideal_support_mass']:.3f} | "
                f"{diag['braket_dm_feed_forward']['ideal_support_mass']:.3f} | "
                f"{diag['local_emulator']['ideal_support_mass']:.3f} |"
            )
        lines.append("")
        lines.append(diagnostics[0][1]["note"])
        lines.append("")
    sampling = report["sampling"]
    shots_list = list(sampling["shots_list"])
    lines.append("## 2. 標本 TVD の床（理想分布を有限 shots でサンプルしたとき）")
    lines.append("")
    lines.append("| shots | TVD 平均 (MC) | 97.5 パーセンタイル | 近似式 Σ√(p(1−p)/(2πn)) |")
    lines.append("|---:|---:|---:|---:|")
    for row in sampling["ideal"]["rows"]:
        lines.append(
            f"| {row['shots']} | {row['tvd_to_expected']['mean']:.3f} | "
            f"{row['tvd_to_expected']['p975']:.3f} | {row['floor_formula']:.3f} |"
        )
    lines.append("")
    picks = _pick_shots(shots_list)
    lines.append("## 3. 有限 shots での λ 推定（厳密分布からの多項サンプリング、平均）")
    lines.append("")
    header = "| 構成 | λ exact (TVD) | λ exact (サポート) |"
    align = "|---|---:|---:|"
    for n in picks:
        header += f" λ TVD @{n} | λ サポート @{n} |"
        align += "---:|---:|"
    lines.append(header)
    lines.append(align)
    for name, sweep in sampling.items():
        if name in ("shots_list", "ideal"):
            continue
        row_text = (
            f"| {name} | {sweep['exact_signal_fraction_tvd']:.3f} | "
            f"{sweep['exact_signal_fraction_support']:.3f} |"
        )
        by_shots = {row["shots"]: row for row in sweep["rows"]}
        for n in picks:
            row = by_shots[n]
            row_text += (
                f" {row['signal_fraction_tvd']['mean']:.3f} | "
                f"{row['signal_fraction_support']['mean']:.3f} ± "
                f"{row['signal_fraction_support']['sd']:.3f} |"
            )
        lines.append(row_text)
    lines.append("")
    lines.append(
        "TVD 由来の λ は標本床のぶん常に低く出る（shots に依存する系統誤差）。"
        "サポート質量由来の λ は shots によらず平均が厳密値に一致し、標準誤差だけが縮む。"
    )
    lines.append("")
    return "\n".join(lines)
