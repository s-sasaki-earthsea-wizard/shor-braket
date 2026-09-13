# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Generate an educational, self-contained walkthrough from a simulation report."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import Any, cast

import numpy as np
from matplotlib import pyplot as plt
from numpy.typing import NDArray

from shor_braket.visualization.style import (
    AMBER,
    BLUE,
    CLASSICAL,
    GREEN,
    INK,
    MUTED,
    PURPLE,
    QUANTUM,
    RED,
)
from shor_braket.visualization.style import (
    draw_arrow as _arrow,
)
from shor_braket.visualization.style import (
    draw_box as _box,
)
from shor_braket.visualization.style import (
    save_figure as _save,
)
from shor_braket.visualization.style import (
    style_figure as _style_figure,
)


def _section(report: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = report.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"report is missing the {name!r} section")
    return cast(dict[str, Any], value)


def _education(report: Mapping[str, Any]) -> dict[str, Any]:
    return _section(report, "education")


def _plot_overview(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    problem = _section(report, "problem")
    circuit = _section(report, "circuit")
    education = _education(report)
    measurement = cast(list[dict[str, Any]], education["phase_candidates"])
    useful = next((row for row in measurement if row["factorization"]), measurement[0])
    factors = problem["factorization"]
    factor_text = (
        f"gcd split\n{problem['modulus']} = {factors[0]} × {factors[1]}"
        if factors
        else "gcd split\nno factors"
    )
    boxes = [
        (
            "1  Choose inputs",
            f"N = {problem['modulus']}, a = {education['base']}\ngcd(a, N) = 1",
            CLASSICAL,
        ),
        (
            "2  Superposition",
            f"H on {circuit['count_qubits']} count qubits\nall x at once",
            QUANTUM,
        ),
        (
            "3  Modular power",
            f"f(x) = {education['base']}^x mod {problem['modulus']}\nperiodic entanglement",
            QUANTUM,
        ),
        (
            "4  Inverse QFT",
            f"period -> phase peaks\nr = {problem['classical_order']}",
            QUANTUM,
        ),
        (
            "5  Measure",
            f"y = {useful['count_value']}\ny / 2^t = {useful['phase_fraction']}",
            QUANTUM,
        ),
        ("6  Classical finish", factor_text, CLASSICAL),
    ]

    figure, ax = plt.subplots(figsize=(16, 4.7))
    _style_figure(figure)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 4.5)
    ax.axis("off")
    ax.set_title(
        f"How Shor factors N = {problem['modulus']}",
        loc="left",
        fontsize=22,
        fontweight="bold",
        color=INK,
        pad=16,
    )
    ax.text(
        0,
        4.05,
        "Yellow stages run classically; blue stages are the quantum period-finding core.",
        color=MUTED,
        fontsize=11,
    )
    width = 2.25
    gap = 0.4
    for index, (heading, detail, color) in enumerate(boxes):
        x = index * (width + gap)
        _box(ax, x, 1.3, width, 1.8, f"{heading}\n\n{detail}", color=color, fontsize=10.5)
        if index < len(boxes) - 1:
            _arrow(ax, (x + width + 0.05, 2.2), (x + width + gap - 0.05, 2.2))
    ax.text(
        0,
        0.55,
        "This run uses an exact local reference circuit. "
        "The dense unitary is intentionally QPU-ineligible.",
        color=MUTED,
        fontsize=10,
    )
    return _save(figure, output_dir, "01_algorithm_overview")


def _plot_circuit(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    problem = _section(report, "problem")
    circuit = _section(report, "circuit")
    education = _education(report)
    count_qubits = int(circuit["count_qubits"])
    work_qubits = int(circuit["work_qubits"])

    figure, ax = plt.subplots(figsize=(14, 4.6))
    _style_figure(figure)
    ax.set_xlim(0, 14)
    ax.set_ylim(-0.5, 4.1)
    ax.axis("off")
    ax.set_title(
        "Quantum period-finding register flow",
        loc="left",
        fontsize=20,
        fontweight="bold",
        color=INK,
        pad=12,
    )
    ax.hlines([2.65, 0.95], 1.7, 13.3, color=INK, lw=1.8)
    ax.text(0.05, 2.65, f"count |0> x {count_qubits}", va="center", color=INK, fontsize=11)
    ax.text(0.05, 0.95, f"work  |1> x {work_qubits}", va="center", color=INK, fontsize=11)
    _box(ax, 2.1, 2.15, 1.5, 1.0, f"H on {count_qubits}", color=BLUE, fontsize=13)
    _box(
        ax,
        4.5,
        0.45,
        3.0,
        2.7,
        f"U_f  modular power\nf(x) = {education['base']}^x mod {problem['modulus']}",
        color=PURPLE,
        fontsize=11,
    )
    _box(ax, 8.5, 2.15, 1.7, 1.0, "inverse QFT", color=BLUE, fontsize=12)
    _box(ax, 11.1, 2.15, 1.4, 1.0, "measure", color=GREEN, fontsize=12)
    ax.annotate(
        "classical y",
        xy=(13.25, 2.65),
        xytext=(12.65, 3.45),
        arrowprops={"arrowstyle": "-|>", "color": GREEN, "lw": 1.5},
        color=GREEN,
        ha="center",
        fontsize=10,
    )
    ax.text(
        4.5,
        -0.12,
        "The local oracle is a dense matrix used to verify the mathematics before a "
        "gate-decomposed QPU circuit exists.",
        color=MUTED,
        fontsize=10,
    )
    return _save(figure, output_dir, "02_quantum_circuit")


def _plot_period(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    problem = _section(report, "problem")
    education = _education(report)
    orbit = cast(list[dict[str, int]], education["modular_orbit"])
    order = int(problem["classical_order"])
    modulus = int(problem["modulus"])
    base = int(education["base"])
    exponents = np.arange(0, max(2 * order, order + 1) + 1)
    values = np.array([pow(base, int(exponent), modulus) for exponent in exponents])

    figure, ax = plt.subplots(figsize=(12, 5.3))
    _style_figure(figure)
    ax.plot(exponents, values, color=PURPLE, lw=2, marker="o", markersize=8)
    for exponent, value in zip(exponents, values, strict=True):
        ax.annotate(
            str(value),
            (int(exponent), int(value)),
            xytext=(0, 11),
            textcoords="offset points",
            ha="center",
            color=INK,
            fontsize=10,
        )
    for boundary in range(order, int(exponents[-1]) + 1, order):
        ax.axvline(boundary, color=AMBER, ls="--", lw=1.3, alpha=0.8)
    ax.set_xticks(exponents)
    ax.set_yticks(sorted({int(row["value"]) for row in orbit}))
    ax.set_xlabel("exponent x")
    ax.set_ylabel(f"{base}^x mod {modulus}")
    ax.set_title(
        f"The modular-power orbit repeats every r = {order}",
        loc="left",
        fontsize=20,
        fontweight="bold",
        color=INK,
        pad=32,
    )
    ax.text(
        0,
        1.015,
        " -> ".join(str(row["value"]) for row in orbit),
        transform=ax.transAxes,
        color=MUTED,
        fontsize=11,
    )
    ax.grid(axis="y", color="#CBD5E1", alpha=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    return _save(figure, output_dir, "03_modular_period")


def _state_matrices(
    report: Mapping[str, Any],
) -> tuple[list[int], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    problem = _section(report, "problem")
    circuit = _section(report, "circuit")
    validation = _section(report, "validation")
    education = _education(report)
    modulus = int(problem["modulus"])
    base = int(education["base"])
    count_qubits = int(circuit["count_qubits"])
    scale = 1 << count_qubits
    work_values = sorted({pow(base, x, modulus) for x in range(scale)})
    row_by_work = {value: index for index, value in enumerate(work_values)}
    prepared = np.zeros((len(work_values), scale), dtype=np.float64)
    entangled = np.zeros_like(prepared)
    transformed = np.zeros_like(prepared)
    prepared[row_by_work[1], :] = 1.0 / scale
    for x_value in range(scale):
        entangled[row_by_work[pow(base, x_value, modulus)], x_value] = 1.0 / scale
    support = cast(list[dict[str, Any]], validation["exact_support"])
    for state in support:
        work_value = int(state["work_value"])
        if work_value in row_by_work:
            transformed[row_by_work[work_value], int(state["count_value"])] += float(
                state["probability"]
            )
    return work_values, prepared, entangled, transformed


def _plot_state_evolution(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    circuit = _section(report, "circuit")
    scale = 1 << int(circuit["count_qubits"])
    work_values, prepared, entangled, transformed = _state_matrices(report)
    panels = [
        ("A  After Hadamards", prepared, "all count values; work = 1"),
        ("B  After modular power", entangled, "periodic count-work stripes"),
        ("C  After inverse QFT", transformed, "probability concentrates at phase peaks"),
    ]

    figure, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True)
    _style_figure(figure)
    axes_array = cast(NDArray[Any], np.asarray(axes))
    for ax, (title, matrix, subtitle) in zip(axes_array, panels, strict=True):
        image = ax.imshow(
            matrix,
            aspect="auto",
            interpolation="nearest",
            cmap="viridis",
            extent=(-0.5, scale - 0.5, len(work_values) - 0.5, -0.5),
        )
        ax.set_yticks(range(len(work_values)), labels=[str(value) for value in work_values])
        ax.set_ylabel("work value")
        ax.set_title(title, loc="left", color=INK, fontsize=14, fontweight="bold")
        ax.text(1.0, 1.06, subtitle, transform=ax.transAxes, ha="right", color=MUTED, fontsize=9)
        figure.colorbar(image, ax=ax, fraction=0.016, pad=0.012, label="joint probability")
    tick_step = max(1, scale // 8)
    ticks = list(range(0, scale, tick_step))
    if scale - 1 not in ticks:
        ticks.append(scale - 1)
    axes_array[-1].set_xticks(ticks)
    axes_array[-1].set_xlabel(
        "count-register value x (or measured phase value y after inverse QFT)"
    )
    figure.suptitle(
        "Where the quantum state changes",
        x=0.08,
        ha="left",
        fontsize=20,
        fontweight="bold",
        color=INK,
    )
    figure.subplots_adjust(top=0.9, hspace=0.56)
    return _save(figure, output_dir, "04_quantum_state_evolution")


def _count_distributions(
    report: Mapping[str, Any],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    circuit = _section(report, "circuit")
    validation = _section(report, "validation")
    sampled = _section(report, "sampled")
    count_qubits = int(circuit["count_qubits"])
    scale = 1 << count_qubits
    ideal = np.zeros(scale, dtype=np.float64)
    for state in cast(list[dict[str, Any]], validation["exact_support"]):
        ideal[int(state["count_value"])] += float(state["probability"])
    sampled_counts: defaultdict[int, int] = defaultdict(int)
    raw_counts = cast(dict[str, int], sampled["measurement_counts"])
    for measured_state, count in raw_counts.items():
        sampled_counts[int(measured_state[:count_qubits], 2)] += int(count)
    shots = sum(sampled_counts.values())
    observed = np.zeros(scale, dtype=np.float64)
    for value, count in sampled_counts.items():
        observed[value] = count / shots
    return ideal, observed


def _plot_measurement(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    execution = _section(report, "execution")
    education = _education(report)
    circuit = _section(report, "circuit")
    scale = 1 << int(circuit["count_qubits"])
    ideal, observed = _count_distributions(report)
    x_values = np.arange(scale)
    bar_width = max(0.8, scale / 180)

    figure, ax = plt.subplots(figsize=(13, 5.8))
    _style_figure(figure)
    ax.bar(
        x_values,
        ideal,
        width=bar_width,
        color=BLUE,
        alpha=0.68,
        label="ideal probability",
    )
    observed_support = np.flatnonzero(observed > 0)
    ax.scatter(
        observed_support,
        observed[observed_support],
        color=AMBER,
        marker="D",
        s=42,
        zorder=4,
        label=f"sampled frequency ({execution['shots']} shots)",
    )
    candidates = cast(list[dict[str, Any]], education["phase_candidates"])
    for row in candidates:
        value = int(row["count_value"])
        if ideal[value] <= 0:
            continue
        annotation_height = max(float(ideal[value]), float(observed[value]))
        ax.annotate(
            f"y={value}\n{row['phase_fraction']}",
            (value, annotation_height),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            color=INK,
            fontsize=9,
        )
    ax.set_xlim(-max(1, scale * 0.025), scale - 1 + max(1, scale * 0.025))
    ax.set_ylim(0, max(float(ideal.max()), float(observed.max())) * 1.28)
    ax.set_xlabel(f"measured count value y (scale = 2^t = {scale})")
    ax.set_ylabel("probability / frequency")
    ax.set_title(
        "Inverse QFT turns the hidden period into measurable peaks",
        loc="left",
        fontsize=20,
        fontweight="bold",
        color=INK,
        pad=14,
    )
    ax.legend(frameon=False, loc="upper right")
    ax.grid(axis="y", color="#CBD5E1", alpha=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    return _save(figure, output_dir, "05_measurement_peaks")


def _plot_postprocessing(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    problem = _section(report, "problem")
    education = _education(report)
    candidates = cast(list[dict[str, Any]], education["phase_candidates"])
    row = next((candidate for candidate in candidates if candidate["factorization"]), None)
    if row is None:
        row = next(
            (candidate for candidate in candidates if candidate["order_candidate"]),
            candidates[0],
        )
    modulus = int(problem["modulus"])
    base = int(education["base"])
    order = row["order_candidate"]
    root = row["half_power"]
    factors = row["factorization"]

    figure, ax = plt.subplots(figsize=(11, 9))
    _style_figure(figure)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_title(
        "From one phase sample to the factors",
        loc="left",
        fontsize=21,
        fontweight="bold",
        color=INK,
        pad=14,
    )
    _box(
        ax,
        2.5,
        8.35,
        5,
        0.85,
        f"measurement: y = {row['count_value']}  ->  y / 2^t = {row['phase_fraction']}",
        color=BLUE,
    )
    _arrow(ax, (5, 8.32), (5, 7.72))
    _box(
        ax,
        2.5,
        6.85,
        5,
        0.85,
        f"continued fraction denominator q = {row['denominator']}",
        color=PURPLE,
    )
    _arrow(ax, (5, 6.82), (5, 6.22))
    order_text = (
        f"order candidate r = {order}:  {base}^{order} mod {modulus} = 1"
        if order
        else "no order candidate from this sample"
    )
    _box(ax, 2.5, 5.35, 5, 0.85, order_text, color=PURPLE)
    _arrow(ax, (5, 5.32), (5, 4.72))
    root_text = (
        f"r is even; x = {base}^(r/2) mod {modulus} = {root}"
        if root is not None
        else "the order cannot be used for the gcd step"
    )
    _box(ax, 2.5, 3.85, 5, 0.85, root_text, color=AMBER)

    if factors:
        _arrow(ax, (4.35, 3.82), (2.5, 2.95))
        _arrow(ax, (5.65, 3.82), (7.5, 2.95))
        _box(
            ax,
            0.45,
            2.05,
            4.1,
            0.85,
            f"gcd({root} - 1, {modulus}) = {factors[0]}",
            color=GREEN,
        )
        _box(
            ax,
            5.45,
            2.05,
            4.1,
            0.85,
            f"gcd({root} + 1, {modulus}) = {factors[1]}",
            color=GREEN,
        )
        _arrow(ax, (2.5, 2.02), (4.4, 1.3))
        _arrow(ax, (7.5, 2.02), (5.6, 1.3))
        _box(
            ax,
            3.2,
            0.45,
            3.6,
            0.8,
            f"{modulus} = {factors[0]} × {factors[1]}",
            color=GREEN,
            fontsize=15,
        )
    else:
        _arrow(ax, (5, 3.82), (5, 2.95))
        reason = str(row["reason"])
        _box(ax, 2.1, 1.9, 5.8, 1.0, f"Rejected: {reason}", color=RED)
    return _save(figure, output_dir, "06_classical_postprocessing")


def _factor_sentence(report: Mapping[str, Any]) -> str:
    problem = _section(report, "problem")
    factors = problem["factorization"]
    if factors:
        return f"最終的に **{problem['modulus']} = {factors[0]} × {factors[1]}** を得た。"
    return (
        f"このケースでは位数 {problem['classical_order']} は得られたが、"
        "非自明な因数は得られなかった。別の底 a で再試行するケースに相当する。"
    )


def _qpu_cost_markdown(report: Mapping[str, Any]) -> str:
    future_costs = _section(report, "future_qpu_costs")
    estimates = cast(list[dict[str, Any]], future_costs["estimates"])
    rows = "\n".join(
        f"| {row['name']} | {row['shots_per_task']} | ${row['estimated_cost_usd']} |"
        for row in estimates
    )
    return f"| Future QPU candidate | shots | One-task estimate |\n|---|---:|---:|\n{rows}"


def _walkthrough_markdown(report: Mapping[str, Any]) -> str:
    problem = _section(report, "problem")
    circuit = _section(report, "circuit")
    execution = _section(report, "execution")
    sampled = _section(report, "sampled")
    education = _education(report)
    orbit = cast(list[dict[str, int]], education["modular_orbit"])
    factors = problem["factorization"]
    result_line = (
        f"`{problem['modulus']} = {factors[0]} × {factors[1]}`"
        if factors
        else "この試行では非自明な因数なし"
    )
    return f"""# Shor ローカルシミュレーション・ウォークスルー

この教材は同じディレクトリの `result.json` から生成した。
図中の周期、測定ピーク、shots、因数はすべてこのrunの値だ。

## 0. 全体像

![Shor algorithm overview](figures/01_algorithm_overview.svg)

入力は `N={problem["modulus"]}`, `a={education["base"]}`。
古典的な前処理で `gcd(a, N)=1` を確認し、量子回路は因数そのものではなく、
`a^x mod N` の**周期（位数）**を探す。今回の結果は {result_line}。

## 1. 量子回路の役割

![Quantum register flow](figures/02_quantum_circuit.svg)

count register は {circuit["count_qubits"]} qubits、
work register は {circuit["work_qubits"]} qubits。
Hadamardで指数 `x` の重ね合わせを作り、モジュラー累乗で
`x` と `a^x mod N` をもつれさせる。逆QFTが周期情報を位相ピークへ変換し、
count registerを測定する。

この回路のoracleは `{circuit["oracle"]}` で、ローカル検証専用だ。
密行列を使うためQPUには投入できない。

## 2. 隠れた周期

![Modular period](figures/03_modular_period.svg)

値は `{" → ".join(str(row["value"]) for row in orbit)}` と巡回し、
周期は `r={problem["classical_order"]}`。量子部分が見つけたい情報はこの `r` だ。

## 3. 量子状態の変化

![Quantum state evolution](figures/04_quantum_state_evolution.svg)

上段ではすべての指数が同じ確率を持つ。
中段ではモジュラー累乗の周期に沿った縞ができる。
下段では逆QFTによって確率が少数の測定値へ集中する。
色はcount/work同時確率を表す。

## 4. 理想分布と実測shots

![Measurement peaks](figures/05_measurement_peaks.svg)

青がshots=0で計算した厳密分布、橙が `{execution["shots"]}` shots の標本頻度だ。
同時分布のTVDは `{float(sampled["joint_tvd"]):.6f}`。
shotsを変えると橙の点は揺れるが、理想ピークの位置は変わらない。

## 5. 測定値から因数へ

![Classical post-processing](figures/06_classical_postprocessing.svg)

測定値を `y/2^t` として連分数近似し、分母から位数候補を復元する。
偶数の位数 `r` が得られ、`a^(r/2)` が `±1 mod N` でなければ、
`gcd(a^(r/2) ± 1, N)` が非自明な因数を与える。

{_factor_sentence(report)}

## 6. 実行条件と将来のQPU費用

このrunは完全にローカルで、AWS requestはなく費用は `$0.00`。
同じ `{execution["shots"]}` shots を承認済み候補へ1 task投入する場合の概算は次の通りだ。

{_qpu_cost_markdown(report)}

価格確認日と出典は `result.json` の `future_qpu_costs` に記録している。

## 再現情報

- backend: `{execution["backend"]}`
- AWS request: `{execution["aws_requests"]}`
- cost: `${execution["estimated_cost_usd"]}`
- shots: `{execution["shots"]}`
- circuit hash: `{circuit["circuit_hash"]}`
- result data: [`result.json`](result.json)
"""


def _walkthrough_html(report: Mapping[str, Any]) -> str:
    markdown = _walkthrough_markdown(report)
    execution = _section(report, "execution")
    circuit = _section(report, "circuit")
    future_costs = _section(report, "future_qpu_costs")
    sections = [
        (
            "全体像",
            "量子計算は因数を直接返さず、周期を見つけて古典計算へ渡す。",
            "01_algorithm_overview.svg",
        ),
        (
            "量子回路",
            "重ね合わせ、モジュラー累乗、逆QFT、測定の順に進む。",
            "02_quantum_circuit.svg",
        ),
        ("周期", "モジュラー累乗列の繰り返しが、探している位数 r だ。", "03_modular_period.svg"),
        (
            "状態の変化",
            "一様な重ね合わせから周期的な縞、位相ピークへ変化する。",
            "04_quantum_state_evolution.svg",
        ),
        ("測定", "理想確率と有限shotsの標本頻度を重ねて比較する。", "05_measurement_peaks.svg"),
        (
            "古典後処理",
            "連分数で位数を復元し、2本のgcd計算で因数へ分岐する。",
            "06_classical_postprocessing.svg",
        ),
    ]
    cards = "\n".join(
        f"""<section><h2>{index}. {escape(title)}</h2><p>{escape(description)}</p>
<img src="figures/{filename}" alt="{escape(title)}"></section>"""
        for index, (title, description, filename) in enumerate(sections)
    )
    problem = _section(report, "problem")
    factor_sentence = escape(_factor_sentence(report).replace("**", ""))
    cost_rows = "\n".join(
        "<tr>"
        f"<td>{escape(str(row['name']))}</td>"
        f"<td>{row['shots_per_task']}</td>"
        f"<td>${row['estimated_cost_usd']}</td>"
        "</tr>"
        for row in cast(list[dict[str, Any]], future_costs["estimates"])
    )
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shor simulation walkthrough: N={problem["modulus"]}</title>
<style>
:root {{ color-scheme: light; color: #172033; background: #eaf0f8;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
body {{ margin: 0; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 48px 24px 80px; }}
header, section {{ background: #fff; border: 1px solid #dbe4f0; border-radius: 16px;
  padding: 28px; margin-bottom: 22px; box-shadow: 0 8px 24px #31425f12; }}
h1 {{ margin: 0 0 12px; font-size: clamp(28px, 4vw, 48px); }}
h2 {{ margin: 0 0 8px; }} p {{ line-height: 1.8; }}
.result {{ color: #166534; font-size: 1.3rem; font-weight: 700; }}
.metrics {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }}
.metric {{ background: #eef2ff; border-radius: 999px; padding: 8px 14px; }}
img {{ width: 100%; height: auto; border-radius: 8px; background: #f8fafc; }}
code {{ background: #eef2f7; border-radius: 4px; padding: 2px 5px; }}
details {{ margin-top: 20px; }} pre {{ white-space: pre-wrap; font-size: 12px; }}
table {{ width: 100%; border-collapse: collapse; }} th, td {{ padding: 10px; text-align: left;
  border-bottom: 1px solid #dbe4f0; }} th:nth-child(n+2), td:nth-child(n+2) {{ text-align: right; }}
</style>
</head><body><main><header><h1>Shor因数分解の可視化</h1>
<p>runの実測データを、入力から因数復元まで順に追う。</p>
<p class="result">{factor_sentence}</p>
<div class="metrics"><span class="metric">shots: {execution["shots"]}</span>
<span class="metric">local cost: ${execution["estimated_cost_usd"]}</span>
<span class="metric">qubits: {circuit["total_qubits"]}</span>
<span class="metric">AWS requests: {str(execution["aws_requests"]).lower()}</span></div></header>
{cards}
<section><h2>6. 将来のQPU費用</h2>
<p>同じshotsを各候補へ1 task投入すると仮定した概算。価格確認日はresult.jsonに記録している。</p>
<table><thead><tr><th>Device</th><th>shots</th><th>Estimate</th></tr></thead>
<tbody>{cost_rows}</tbody></table></section>
<details><summary>Markdown版の内容</summary><pre>{escape(markdown)}</pre></details>
</main></body></html>
"""


def generate_walkthrough(report: Mapping[str, Any], artifact_dir: Path) -> dict[str, Any]:
    """Write SVG/PNG figures and HTML/Markdown walkthroughs for one run."""
    figure_dir = artifact_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    figures = {
        "algorithm_overview": _plot_overview(report, figure_dir),
        "quantum_circuit": _plot_circuit(report, figure_dir),
        "modular_period": _plot_period(report, figure_dir),
        "quantum_state_evolution": _plot_state_evolution(report, figure_dir),
        "measurement_peaks": _plot_measurement(report, figure_dir),
        "classical_postprocessing": _plot_postprocessing(report, figure_dir),
    }
    markdown_path = artifact_dir / "walkthrough.md"
    html_path = artifact_dir / "walkthrough.html"
    markdown_path.write_text(_walkthrough_markdown(report), encoding="utf-8")
    html_path.write_text(_walkthrough_html(report), encoding="utf-8")
    return {
        "generator": "matplotlib",
        "figures": figures,
        "walkthrough_markdown": markdown_path.name,
        "walkthrough_html": html_path.name,
    }
