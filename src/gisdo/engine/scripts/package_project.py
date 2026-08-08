#!/usr/bin/env python
"""Create a new portable PPKX with the official GeoScene PackageProject tool."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import arcpy
from inspect_aprx import inspect_project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("output_ppkx", help="New PPKX path; must not exist.")
    parser.add_argument("--summary", default="Portable GeoScene project package")
    parser.add_argument("--tags", default="GeoScene;GIS;ArcPy")
    parser.add_argument("--allow-broken", action="store_true")
    args = parser.parse_args()

    project = os.path.abspath(args.project)
    output = Path(args.output_ppkx).expanduser().resolve()
    if not os.path.isfile(project):
        raise FileNotFoundError(project)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite: {output}")

    inventory = inspect_project(project)
    if inventory["broken_count"] and not args.allow_broken:
        raise RuntimeError(
            f"Saved APRX has {inventory['broken_count']} broken source(s); refusing to package."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    arcpy.env.overwriteOutput = False
    result = arcpy.management.PackageProject(
        in_project=project,
        output_file=str(output),
        sharing_internal="EXTERNAL",
        package_as_template="PROJECT_PACKAGE",
        extent=None,
        apply_extent_to_arcsde="ALL",
        additional_files=None,
        summary=args.summary,
        tags=args.tags,
        version="CURRENT",
        include_toolboxes="TOOLBOXES",
        include_history_items="NO_HISTORY_ITEMS",
        read_only="READ_WRITE",
        select_related_rows="KEEP_ALL_RELATED_ROWS",
        preserve_sqlite="PRESERVE_SQLITE",
    )

    report = {
        "project": project,
        "output_ppkx": str(output),
        "output_exists": output.exists(),
        "output_bytes": output.stat().st_size if output.exists() else None,
        "maps_count": inventory["maps_count"],
        "layouts_count": inventory["layouts_count"],
        "layers_count": inventory["layers_count"],
        "broken_count": inventory["broken_count"],
        "tool_output": result.getOutput(0),
        "messages": arcpy.GetMessages(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if output.exists() else 3


if __name__ == "__main__":
    raise SystemExit(main())
