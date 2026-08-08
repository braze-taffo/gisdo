#!/usr/bin/env python
"""Read-only inventory of a GeoScene/ArcGIS workspace such as a file GDB."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import arcpy


def inspect_workspace(workspace: str, include_counts: bool = True) -> dict:
    workspace = os.path.abspath(workspace)
    if not arcpy.Exists(workspace):
        raise FileNotFoundError(workspace)

    items = []
    for dirpath, _dirnames, names in arcpy.da.Walk(
        workspace,
        datatype=["FeatureClass", "Table", "RasterDataset"],
    ):
        for name in sorted(names, key=str.casefold):
            path = os.path.join(dirpath, name)
            desc = arcpy.Describe(path)
            spatial_reference = getattr(desc, "spatialReference", None)
            count = None
            if include_counts and getattr(desc, "dataType", None) != "RasterDataset":
                try:
                    count = int(arcpy.management.GetCount(path).getOutput(0))
                except Exception:
                    count = None
            items.append(
                {
                    "name": name,
                    "relative_path": os.path.relpath(path, workspace),
                    "data_type": getattr(desc, "dataType", None),
                    "shape_type": getattr(desc, "shapeType", None),
                    "count": count,
                    "spatial_reference": getattr(spatial_reference, "name", None),
                    "wkid": getattr(spatial_reference, "factoryCode", None),
                }
            )

    physical_files = []
    if os.path.isdir(workspace):
        physical_files = [path for path in Path(workspace).rglob("*") if path.is_file()]

    return {
        "workspace": workspace,
        "dataset_count": len(items),
        "physical_file_count": len(physical_files),
        "physical_bytes": sum(path.stat().st_size for path in physical_files),
        "items": items,
    }


def write_new(path: str, text: str) -> None:
    output = Path(path).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace")
    parser.add_argument("--skip-counts", action="store_true")
    parser.add_argument("--output", help="Optional new JSON report path. Must not exist.")
    args = parser.parse_args()

    report = inspect_workspace(args.workspace, include_counts=not args.skip_counts)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if args.output:
        write_new(args.output, text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
