#!/usr/bin/env python
"""Validate a PPKX using official ExtractPackage and reopen its extracted APRX."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import arcpy
from inspect_aprx import inspect_project


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def primary_aprx(output: Path) -> Path:
    ordered = []
    for version in ("p30", "p20"):
        folder = output / version
        if folder.exists():
            ordered.extend(sorted(folder.glob("*.aprx"), key=lambda p: p.name.casefold()))
    ordered.extend(sorted(output.glob("*.aprx"), key=lambda p: p.name.casefold()))
    if not ordered:
        ordered.extend(sorted(output.rglob("*.aprx"), key=lambda p: str(p).casefold()))
    if not ordered:
        raise FileNotFoundError(f"No APRX found after extracting package to {output}")
    return ordered[0]


def layout_signature(report: dict) -> list[dict]:
    return [
        {"name": layout["name"], "surrounds": layout["map_surround_signature"]}
        for layout in report["layouts"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    parser.add_argument("output_dir", help="New extraction directory; must not exist.")
    parser.add_argument("--source-aprx", help="Optional source APRX for layout-signature comparison.")
    args = parser.parse_args()

    package = Path(args.package).expanduser().resolve()
    output = Path(args.output_dir).expanduser().resolve()
    if not package.is_file():
        raise FileNotFoundError(package)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite or reuse: {output}")

    arcpy.env.overwriteOutput = False
    result = arcpy.management.ExtractPackage(
        in_package=str(package),
        output_folder=str(output),
        cache_package="NO_CACHE",
    )
    extracted_aprx = primary_aprx(output)
    extracted_report = inspect_project(str(extracted_aprx))

    source_report = None
    signatures_match = None
    if args.source_aprx:
        source_report = inspect_project(args.source_aprx)
        signatures_match = layout_signature(source_report) == layout_signature(extracted_report)

    files = [path for path in output.rglob("*") if path.is_file()]
    passed = extracted_report["broken_count"] == 0 and signatures_match is not False
    report = {
        "package": str(package),
        "package_bytes": package.stat().st_size,
        "package_sha256": sha256(package),
        "output_dir": str(output),
        "extracted_file_count": len(files),
        "extracted_bytes": sum(path.stat().st_size for path in files),
        "extracted_aprx": str(extracted_aprx),
        "maps_count": extracted_report["maps_count"],
        "layouts_count": extracted_report["layouts_count"],
        "layers_count": extracted_report["layers_count"],
        "tables_count": extracted_report["tables_count"],
        "broken_count": extracted_report["broken_count"],
        "broken": extracted_report["broken"],
        "layout_signatures_match": signatures_match,
        "passed": passed,
        "tool_output": result.getOutput(0),
        "messages": arcpy.GetMessages(),
    }
    report_path = output / "validation_report.json"
    with report_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
