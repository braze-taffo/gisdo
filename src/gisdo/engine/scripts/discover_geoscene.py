#!/usr/bin/env python
"""Locate and read-only probe local GeoScene Pro or ArcGIS Pro ArcPy runtimes."""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

EXTENSIONS = ["3D", "Spatial", "Network", "ImageAnalyst", "GeoStats", "DataReviewer"]
PYTHON_PACKAGES = [
    "arcpy",
    "numpy",
    "pandas",
    "geopandas",
    "rasterio",
    "networkx",
    "sklearn",
    "xgboost",
    "shap",
]


def has_arcpy() -> bool:
    return importlib.util.find_spec("arcpy") is not None


def candidate_runtimes() -> list[str]:
    candidates: list[str] = []
    explicit = os.environ.get("GEOSCENE_PYTHON")
    if explicit:
        candidates.append(explicit)
    arcgis_pro_explicit = os.environ.get("ARCGISPRO_PYTHON")
    if arcgis_pro_explicit:
        candidates.append(arcgis_pro_explicit)
    if has_arcpy():
        candidates.append(sys.executable)

    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        r"C:\Program Files",
    ]
    for root in filter(None, roots):
        candidates.extend(
            glob.glob(
                os.path.join(
                    root,
                    "GeoScene",
                    "Pro",
                    "bin",
                    "Python",
                    "envs",
                    "*",
                    "python.exe",
                )
            )
        )
        candidates.extend(
            glob.glob(
                os.path.join(
                    root,
                    "ArcGIS",
                    "Pro",
                    "bin",
                    "Python",
                    "envs",
                    "*",
                    "python.exe",
                )
            )
        )

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(Path(candidate).expanduser().resolve())
        key = os.path.normcase(resolved)
        if key not in seen and os.path.isfile(resolved):
            seen.add(key)
            result.append(resolved)
    return result


def legacy_arcmap_candidates() -> list[str]:
    candidates = []
    explicit = os.environ.get("ARCMAP_PYTHON")
    if explicit:
        candidates.append(explicit)
    candidates.extend(glob.glob(r"C:\Python27\ArcGIS*\python.exe"))
    candidates.extend(glob.glob(r"C:\Python27\ArcGISx64*\python.exe"))
    result = []
    seen = set()
    for candidate in candidates:
        resolved = str(Path(candidate).expanduser().resolve())
        key = os.path.normcase(resolved)
        if key not in seen and os.path.isfile(resolved):
            seen.add(key)
            result.append(resolved)
    return result


def probe_current_runtime() -> dict:
    import arcpy

    extensions = {}
    for code in EXTENSIONS:
        try:
            extensions[code] = arcpy.CheckExtension(code)
        except Exception as exc:  # pragma: no cover - product-specific
            extensions[code] = f"ERROR: {exc}"

    install = arcpy.GetInstallInfo()
    install_dir = str(install.get("InstallDir", "")).lower()
    if "geoscene" in install_dir:
        family = "GeoScene Pro"
    elif "arcgis" in install_dir:
        family = "ArcGIS Pro"
    else:
        family = "ArcPy-compatible Pro runtime"
    return {
        "runtime_family": family,
        "runtime_python": sys.executable,
        "install": install,
        "product": arcpy.ProductInfo(),
        "extensions": extensions,
        "toolboxes": sorted(arcpy.ListToolboxes() or []),
        "tool_count": len(arcpy.ListTools() or []),
        "python_packages": {
            name: importlib.util.find_spec(name) is not None for name in PYTHON_PACKAGES
        },
        "legacy_arcmap_candidates": legacy_arcmap_candidates(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only list candidate interpreters; do not start ArcPy.",
    )
    args = parser.parse_args()

    if args.inside or has_arcpy():
        if not has_arcpy():
            print(json.dumps({"error": "ArcPy is unavailable", "runtime": sys.executable}, indent=2))
            return 2
        print(json.dumps(probe_current_runtime(), ensure_ascii=False, indent=2, default=str))
        return 0

    candidates = candidate_runtimes()
    if args.list_only:
        print(
            json.dumps(
                {"modern_candidates": candidates, "legacy_arcmap_candidates": legacy_arcmap_candidates()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if candidates or legacy_arcmap_candidates() else 2

    failures = []
    for runtime in candidates:
        completed = subprocess.run(
            [runtime, "-X", "utf8", os.path.abspath(__file__), "--inside"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode == 0:
            sys.stdout.write(completed.stdout)
            return 0
        failures.append(
            {"runtime": runtime, "returncode": completed.returncode, "stderr": completed.stderr}
        )

    print(
        json.dumps(
            {
                "error": "No usable GeoScene Pro or ArcGIS Pro ArcPy runtime was found",
                "legacy_arcmap_candidates": legacy_arcmap_candidates(),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
