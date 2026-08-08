#!/usr/bin/env python
"""Render gisdo classified-line JSON to a new PNG and optional vector PDF."""

from __future__ import annotations

import argparse
import bisect
import json
import math
from itertools import pairwise
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
from verify_png import inspect_png

DEFAULT_FIVE_COLORS = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]


def parse_numbers(raw: str) -> list[float]:
    values = [float(item.strip()) for item in raw.split(",") if item.strip()]
    if len(values) < 2 or any(not math.isfinite(value) for value in values):
        raise ValueError("Provide at least two finite class-break values.")
    if any(right <= left for left, right in pairwise(values)):
        raise ValueError("Class breaks must be strictly increasing.")
    return values


def class_colors(raw: str | None, count: int) -> list[str]:
    if raw:
        colors = [item.strip() for item in raw.split(",") if item.strip()]
        if len(colors) != count:
            raise ValueError(f"Expected {count} colors, received {len(colors)}.")
        return colors
    if count == 5:
        return DEFAULT_FIVE_COLORS
    color_map = plt.get_cmap("YlOrRd", count)
    return [matplotlib.colors.to_hex(color_map(index)) for index in range(count)]


def format_number(value: float) -> str:
    return f"{value:g}"


def class_labels(raw: str | None, breaks: list[float]) -> list[str]:
    count = len(breaks) - 1
    if raw:
        labels = [item.strip() for item in raw.split("|")]
        if len(labels) != count:
            raise ValueError(f"Expected {count} labels separated by '|'.")
        return labels
    labels = []
    for index, (lower, upper) in enumerate(pairwise(breaks)):
        bracket = "]" if index == count - 1 else ")"
        labels.append(f"[{format_number(lower)}, {format_number(upper)}{bracket}")
    return labels


def load_bridge(path: str) -> dict:
    source = Path(path).expanduser().resolve()
    with source.open("r", encoding="utf-8") as handle:
        record = json.load(handle)
    if record.get("format") != "gisdo-classified-lines" or record.get("version") != 1:
        raise ValueError("Input is not gisdo-classified-lines version 1 JSON.")
    if not record.get("features"):
        raise ValueError("Input contains no features.")
    return record


def classification_index(value: float, breaks: list[float]) -> int:
    if value < breaks[0] or value > breaks[-1]:
        raise ValueError(
            f"Value {value:g} lies outside declared breaks {breaks[0]:g}..{breaks[-1]:g}."
        )
    index = bisect.bisect_right(breaks, value) - 1
    return min(index, len(breaks) - 2)


def require_new(path: str | None) -> Path | None:
    if path is None:
        return None
    output = Path(path).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def add_scale_bar(ax, length: float, label: str, extent: tuple[float, float, float, float]):
    xmin, ymin, xmax, ymax = extent
    width = xmax - xmin
    height = ymax - ymin
    if length <= 0 or length > width * 0.8:
        raise ValueError("Scale-bar length must be positive and fit within 80% of map width.")
    x0 = xmin + width * 0.06
    y0 = ymin + height * 0.07
    ax.plot([x0, x0 + length], [y0, y0], color="black", linewidth=3, solid_capstyle="butt")
    ax.plot([x0, x0], [y0 - height * 0.01, y0 + height * 0.01], color="black", linewidth=1)
    ax.plot(
        [x0 + length, x0 + length],
        [y0 - height * 0.01, y0 + height * 0.01],
        color="black",
        linewidth=1,
    )
    ax.text(x0 + length / 2.0, y0 + height * 0.018, label, ha="center", va="bottom")


def add_north_arrow(ax):
    ax.annotate(
        "N",
        xy=(0.94, 0.94),
        xytext=(0.94, 0.82),
        xycoords="axes fraction",
        textcoords="axes fraction",
        ha="center",
        va="center",
        fontsize=14,
        fontweight="bold",
        arrowprops={"arrowstyle": "-|>", "facecolor": "black", "edgecolor": "black"},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json")
    parser.add_argument("output_png", help="New PNG path; must not exist.")
    parser.add_argument("--output-pdf", help="Optional new PDF path; must not exist.")
    parser.add_argument("--report", help="Optional new JSON validation report; must not exist.")
    parser.add_argument("--breaks", required=True, help="Comma-separated class boundaries.")
    parser.add_argument("--colors", help="Comma-separated colors; one per class.")
    parser.add_argument("--labels", help="Legend labels separated by '|'.")
    parser.add_argument("--title", default="")
    parser.add_argument("--legend-title", default="")
    parser.add_argument("--scale-bar", type=float, help="Length in dataset coordinate units.")
    parser.add_argument("--scale-label", help="Scale-bar text, for example '2 km'.")
    parser.add_argument("--no-north-arrow", action="store_true")
    parser.add_argument("--axis-km", action="store_true", help="Display coordinate ticks in km.")
    parser.add_argument("--no-grid", action="store_true")
    parser.add_argument("--line-width", type=float, default=1.1)
    parser.add_argument("--width", type=float, default=10.0, help="Figure width in inches.")
    parser.add_argument("--height", type=float, default=6.5, help="Figure height in inches.")
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()

    if args.line_width <= 0 or args.width <= 0 or args.height <= 0 or args.dpi <= 0:
        parser.error("Line width, figure dimensions, and DPI must be positive.")

    output_png = require_new(args.output_png)
    output_pdf = require_new(args.output_pdf)
    output_report = require_new(args.report)
    assert output_png is not None
    if output_png.suffix.lower() != ".png":
        raise ValueError("output_png must use a .png extension.")
    if output_pdf is not None and output_pdf.suffix.lower() != ".pdf":
        raise ValueError("--output-pdf must use a .pdf extension.")
    if output_pdf is not None and output_pdf == output_png:
        raise ValueError("PNG and PDF outputs must use different paths.")
    if output_report is not None and output_report.suffix.lower() != ".json":
        raise ValueError("--report must use a .json extension.")
    if output_report is not None and output_report in {output_png, output_pdf}:
        raise ValueError("The validation report must use a distinct path.")
    breaks = parse_numbers(args.breaks)
    colors = class_colors(args.colors, len(breaks) - 1)
    labels = class_labels(args.labels, breaks)
    bridge = load_bridge(args.input_json)

    value_min = float(bridge["value_min"])
    value_max = float(bridge["value_max"])
    if value_min < breaks[0] or value_max > breaks[-1]:
        raise ValueError(
            f"Declared breaks do not cover data range {value_min:g}..{value_max:g}."
        )

    grouped_segments: list[list[list[list[float]]]] = [[] for _ in colors]
    x_values = []
    y_values = []
    feature_count = 0
    part_count = 0
    for feature in bridge["features"]:
        value = float(feature["value"])
        index = classification_index(value, breaks)
        feature_count += 1
        for part in feature["parts"]:
            if len(part) < 2:
                continue
            points = [[float(point[0]), float(point[1])] for point in part]
            grouped_segments[index].append(points)
            x_values.extend(point[0] for point in points)
            y_values.extend(point[1] for point in points)
            part_count += 1
    if not x_values or not y_values:
        raise ValueError("No drawable line parts were found.")

    extent = (min(x_values), min(y_values), max(x_values), max(y_values))
    xmin, ymin, xmax, ymax = extent
    width = xmax - xmin
    height = ymax - ymin
    if width <= 0 or height <= 0:
        raise ValueError("Map extent must have nonzero width and height.")
    padding_x = width * 0.03
    padding_y = height * 0.03

    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False
    figure, ax = plt.subplots(figsize=(args.width, args.height), dpi=args.dpi)
    figure.patch.set_facecolor("white")
    ax.set_facecolor("white")
    for color, segments in zip(colors, grouped_segments):
        if segments:
            ax.add_collection(LineCollection(segments, colors=[color], linewidths=args.line_width))
    ax.set_xlim(xmin - padding_x, xmax + padding_x)
    ax.set_ylim(ymin - padding_y, ymax + padding_y)
    ax.set_aspect("equal", adjustable="box")
    if not args.no_grid:
        ax.grid(True, color="#d9d9d9", linewidth=0.5, alpha=0.7)
        ax.set_axisbelow(True)
    if args.axis_km:
        formatter = FuncFormatter(lambda value, _position: f"{value / 1000.0:g}")
        ax.xaxis.set_major_formatter(formatter)
        ax.yaxis.set_major_formatter(formatter)
        ax.set_xlabel("X (km)")
        ax.set_ylabel("Y (km)")
    if args.title:
        ax.set_title(args.title, fontsize=16, pad=12)

    handles = [Line2D([0], [0], color=color, linewidth=3, label=label) for color, label in zip(colors, labels)]
    ax.legend(handles=handles, title=args.legend_title or bridge.get("value_field", ""), loc="lower right")

    spatial_reference = bridge.get("spatial_reference") or {}
    if args.scale_bar is not None:
        if str(spatial_reference.get("type", "")).lower() != "projected":
            raise ValueError("A scale bar requires a projected CRS.")
        if not spatial_reference.get("linear_unit"):
            raise ValueError("A scale bar requires a known projected linear unit.")
        add_scale_bar(
            ax,
            args.scale_bar,
            args.scale_label or f"{args.scale_bar:g} {spatial_reference['linear_unit']}",
            extent,
        )
    if not args.no_north_arrow:
        add_north_arrow(ax)

    figure.subplots_adjust(left=0.09, right=0.97, bottom=0.11, top=0.90)
    figure.savefig(output_png, dpi=args.dpi, format="png", facecolor="white")
    if output_pdf is not None:
        figure.savefig(output_pdf, format="pdf", facecolor="white")
    plt.close(figure)

    pixel_report = inspect_png(output_png)
    report = {
        "input_json": str(Path(args.input_json).expanduser().resolve()),
        "output_png": str(output_png),
        "output_png_bytes": output_png.stat().st_size,
        "output_pdf": str(output_pdf) if output_pdf else None,
        "output_pdf_bytes": output_pdf.stat().st_size if output_pdf else None,
        "report": str(output_report) if output_report else None,
        "feature_count": feature_count,
        "part_count": part_count,
        "breaks": breaks,
        "labels": labels,
        "colors": colors,
        "value_min": value_min,
        "value_max": value_max,
        "spatial_reference": spatial_reference,
        "pixel_validation": pixel_report,
        "passed": pixel_report["passed"],
    }
    if output_report is not None:
        with output_report.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
