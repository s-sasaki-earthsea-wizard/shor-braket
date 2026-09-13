# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Shared colours and drawing helpers for the project's Matplotlib figures."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

BLUE = "#2563EB"
GREEN = "#16A34A"
AMBER = "#D97706"
RED = "#DC2626"
PURPLE = "#7C3AED"
INK = "#172033"
MUTED = "#64748B"
PAPER = "#F8FAFC"
CLASSICAL = AMBER
QUANTUM = BLUE


def style_figure(figure: Figure) -> None:
    """Apply the shared paper background."""
    figure.patch.set_facecolor(PAPER)


def draw_box(
    ax: Axes,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    *,
    color: str,
    fontsize: float = 11,
) -> None:
    """Draw a rounded, tinted box with centred text."""
    rgb = to_rgb(color)
    tinted_color = tuple(0.14 * channel + 0.86 for channel in rgb)
    patch = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.03,rounding_size=0.04",
        linewidth=1.6,
        edgecolor=color,
        facecolor=tinted_color,
        zorder=2,
    )
    ax.add_patch(patch)
    ax.text(
        x + width / 2,
        y + height / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        color=INK,
        zorder=3,
        linespacing=1.4,
    )


def draw_arrow(ax: Axes, start: tuple[float, float], end: tuple[float, float]) -> None:
    """Draw a muted arrow between two points."""
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={"arrowstyle": "-|>", "color": MUTED, "lw": 1.8},
    )


def save_figure(figure: Figure, output_dir: Path, stem: str) -> dict[str, str]:
    """Save SVG and PNG versions of a figure and close it."""
    svg_filename = f"{stem}.svg"
    png_filename = f"{stem}.png"
    figure.savefig(
        output_dir / svg_filename,
        bbox_inches="tight",
        facecolor=figure.get_facecolor(),
    )
    figure.savefig(
        output_dir / png_filename,
        bbox_inches="tight",
        dpi=170,
        facecolor=figure.get_facecolor(),
    )
    plt.close(figure)
    return {
        "svg": f"figures/{svg_filename}",
        "png": f"figures/{png_filename}",
    }
