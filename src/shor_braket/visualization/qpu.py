# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Markdown for a hardware run.

The layout of this report follows what the result is allowed to say. The comparison against the
emulator's prediction comes first, because that is the experiment. The order recovery rate comes
last and next to its own baseline, because on its own it is not evidence of anything: a device
returning uniform noise reaches about 0.75 at t = 2.
"""

from __future__ import annotations

from typing import Any


def _format(value: object, digits: int = 3) -> str:
    """Render a number for a table cell, leaving non-numbers alone."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "✅" if value else "❌"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def qpu_markdown(analysis: dict[str, Any]) -> str:
    """Render one task's analysis as Markdown.

    Args:
        analysis: The analysis produced by :func:`~shor_braket.runner.report.analyze_submission`.

    Returns:
        The report text.
    """
    metrics = analysis["metrics"]
    verdict = metrics["verdict"]
    device = analysis.get("device") or {}
    task = analysis.get("task") or {}
    record = analysis.get("validated_record") or {}
    cost = analysis.get("cost") or {}

    predicted = metrics.get("predicted_signal_fraction")
    difference = metrics.get("signal_fraction_difference")
    error = metrics["signal_fraction_support_mass_error"]

    measured = _format(metrics["signal_fraction_support_mass"])
    pass_line = _format(verdict.get("pass_line"), 1)
    sigmas = _format(verdict.get("detection_sigmas"), 1)
    floor = _format(metrics["sampling_floor_estimate"])

    lines = [
        f"# 実機実行の結果: {device.get('name', '?')} / {analysis.get('oracle_mode', '?')}",
        "",
        analysis.get("claim", ""),
        "",
        "## 1. 手掛かりの量 × 機械 → 信号残存率 λ",
        "",
        "| 項目 | 値 |",
        "|---|---:|",
        f"| エミュレーションの予測 λ | {_format(predicted)} |",
        f"| **実機の λ（サポート質量、不偏）** | **{measured} ± {_format(error)}** |",
        f"| 差（実機 − 予測） | {_format(difference)} |",
        f"| 合格線 {pass_line} に対する判定 | {_format(verdict['passed'])} |",
        f"| 信号が検出されたか（λ > {sigmas} σ） | {_format(verdict.get('signal_detected'))} |",
        f"| work が軌道に乗った割合 | {_format(metrics.get('orbit_mass'))} |",
        f"| 下位ビットの可視度（t ≥ 3） | {_format(metrics.get('low_bit_visibility'))} |",
        "",
        "λ は「理想分布と一様分布の混合」とみなしたときの理想側の重み。サポート質量由来の推定量は"
        "カウントに対して線形なので、ショット数によらず不偏である。",
        "",
        "**N = 15 では位数が 2 のべきなので、理想の同時分布は"
        "「許される y」と「軌道上の work」の直積になる。**"
        "t = 2 では y がすべて許されるので、λ は work が軌道に乗った割合を言い換えただけで、"
        "count register のコヒーレンスは見ていない。t ≥ 3 の下位ビットの可視度は、"
        "U^4 = I を制御して |+⟩ のまま待機する count qubit が位相を保てたかを測る"
        "（1 なら理想、0 ならデコヒーレンス）。どちらも count qubit どうしの干渉ではない。",
        "",
        "## 2. 分布の距離",
        "",
        "| 指標 | 値 |",
        "|---|---:|",
        f"| 標本 TVD（理想分布との距離） | {_format(metrics['sampled_tvd'])} |",
        f"| TVD の標本床（完璧な機械でもこれだけ離れる） | {floor} |",
        f"| TVD 由来の λ（**小ショットでは沈む**） | {_format(metrics['signal_fraction_tvd'])} |",
        f"| Hellinger fidelity | {_format(metrics['hellinger_fidelity'])} |",
        f"| 理想サポート上の質量 | {_format(metrics['ideal_support_mass_sampled'])} |",
        "",
        "**TVD 由来の λ は合否に使わない。** 有限ショットの標本は完璧な機械でも"
        "標本床のぶん理想分布から"
        "離れるので、ショット数が少ないほど λ を低く見せる。経路確認のような小さな実行では"
        "この偏りが支配的になる。",
        "",
        "## 3. 位数復元率（参考。合否には使わない）",
        "",
        "| 項目 | 値 |",
        "|---|---:|",
        f"| 標本からの位数復元率 | {_format(metrics['order_recovery_rate_sampled'])} |",
        f"| 一様乱数 y の基準値 | {_format(metrics['order_recovery_baseline_uniform_y'])} |",
        "",
        "t = 2 では一様乱数を返す機械でも基準値に達する。**この数字は何の証拠にもならない**ので、"
        "基準値と並べてのみ記載する（ADR-0001）。",
        "",
        "## 4. 実行の同定",
        "",
        "| 項目 | 値 |",
        "|---|---|",
        f"| タスク | `{task.get('arn', '?')}` |",
        f"| デバイス | {device.get('name', '?')} (`{device.get('key', '?')}`) |",
        f"| オラクル | {analysis.get('oracle_mode', '?')} |",
        f"| ショット数 | {analysis['shots']} |",
        f"| 回路ハッシュ | `{analysis.get('circuit_hash', '?')}` |",
        f"| validated レコード発行 | {record.get('issued_at', '?')} |",
        f"| 校正ハッシュ | `{record.get('capabilities_sha256', '?')}` |",
        f"| 概算コスト | {cost.get('estimated_cost_usd', '?')} USD |",
        f"| レジスタの物理 qubit（count → work） | {analysis['register_order_physical']} |",
        "",
    ]
    return "\n".join(lines) + "\n"
