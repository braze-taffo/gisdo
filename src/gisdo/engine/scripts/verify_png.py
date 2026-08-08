#!/usr/bin/env python
"""Measure PNG pixel content and reject blank or nearly blank exports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np


def inspect_png(
    path: str | Path,
    white_threshold: float = 0.985,
    min_non_white_ratio: float = 0.001,
    min_non_white_pixels: int = 1000,
) -> dict:
    image_path = Path(path).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    array = np.asarray(mpimg.imread(image_path))
    if array.ndim == 2:
        rgb = np.repeat(array[:, :, None], 3, axis=2)
        alpha = None
    elif array.ndim == 3 and array.shape[2] >= 3:
        rgb = array[:, :, :3]
        alpha = array[:, :, 3] if array.shape[2] >= 4 else None
    else:
        raise ValueError(f"Unsupported image array shape: {array.shape}")

    rgb = rgb.astype(float)
    if float(rgb.max()) > 1.0:
        rgb /= 255.0
    if alpha is not None:
        alpha = alpha.astype(float)
        if float(alpha.max()) > 1.0:
            alpha /= 255.0
        rgb = rgb * alpha[:, :, None] + (1.0 - alpha[:, :, None])

    non_white = np.any(rgb < white_threshold, axis=2)
    count = int(non_white.sum())
    total = int(non_white.size)
    ratio = count / total if total else 0.0
    bounding_box = None
    if count:
        rows, columns = np.where(non_white)
        bounding_box = {
            "left": int(columns.min()),
            "top": int(rows.min()),
            "right": int(columns.max()),
            "bottom": int(rows.max()),
        }
    passed = count >= min_non_white_pixels and ratio >= min_non_white_ratio
    return {
        "path": str(image_path),
        "bytes": image_path.stat().st_size,
        "width": int(array.shape[1]),
        "height": int(array.shape[0]),
        "channels": 1 if array.ndim == 2 else int(array.shape[2]),
        "white_threshold": white_threshold,
        "non_white_pixels": count,
        "total_pixels": total,
        "non_white_ratio": ratio,
        "content_bounding_box": bounding_box,
        "minimum_non_white_pixels": min_non_white_pixels,
        "minimum_non_white_ratio": min_non_white_ratio,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("png")
    parser.add_argument("--white-threshold", type=float, default=0.985)
    parser.add_argument("--min-non-white-ratio", type=float, default=0.001)
    parser.add_argument("--min-non-white-pixels", type=int, default=1000)
    parser.add_argument("--output", help="Optional new JSON report path; must not exist.")
    args = parser.parse_args()
    if not 0.0 < args.white_threshold <= 1.0:
        parser.error("--white-threshold must be in (0, 1].")
    if not 0.0 <= args.min_non_white_ratio <= 1.0:
        parser.error("--min-non-white-ratio must be in [0, 1].")
    if args.min_non_white_pixels < 0:
        parser.error("--min-non-white-pixels must be non-negative.")
    report = inspect_png(
        args.png,
        white_threshold=args.white_threshold,
        min_non_white_ratio=args.min_non_white_ratio,
        min_non_white_pixels=args.min_non_white_pixels,
    )
    if args.output:
        output = Path(args.output).expanduser().resolve()
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
