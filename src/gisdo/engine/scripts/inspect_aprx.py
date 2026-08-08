#!/usr/bin/env python
"""Read-only inventory of a saved GeoScene/ArcGIS Pro APRX project."""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
from pathlib import Path

import arcpy


def number(value):
    try:
        result = float(value)
        return round(result, 6) if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def data_source(item):
    try:
        if item.supports("DATASOURCE"):
            return item.dataSource
    except Exception:
        return None
    return None


def named_item(item) -> dict:
    return {
        "name": getattr(item, "name", None),
        "long_name": getattr(item, "longName", None),
        "data_source": data_source(item),
        "is_broken": bool(getattr(item, "isBroken", False)),
    }


def element_record(element) -> dict:
    return {
        "name": getattr(element, "name", None),
        "type": getattr(element, "type", None),
        "x": number(getattr(element, "elementPositionX", None)),
        "y": number(getattr(element, "elementPositionY", None)),
        "width": number(getattr(element, "elementWidth", None)),
        "height": number(getattr(element, "elementHeight", None)),
    }


def inspect_project(project_path: str) -> dict:
    project_path = os.path.abspath(project_path)
    if not os.path.isfile(project_path):
        raise FileNotFoundError(project_path)

    project = arcpy.mp.ArcGISProject(project_path)
    maps = []
    all_sources: set[str] = set()
    broken = []

    for map_obj in project.listMaps():
        layers = [named_item(layer) for layer in map_obj.listLayers()]
        tables = [named_item(table) for table in map_obj.listTables()]
        for record in layers + tables:
            if record["data_source"]:
                all_sources.add(record["data_source"])
            if record["is_broken"]:
                broken.append({"map": map_obj.name, **record})
        maps.append({"name": map_obj.name, "layers": layers, "tables": tables})

    layouts = []
    for layout in project.listLayouts():
        elements = [element_record(element) for element in layout.listElements()]
        surrounds = [
            element_record(element) for element in layout.listElements("MAPSURROUND_ELEMENT")
        ]
        type_counts: dict[str, int] = {}
        for element in elements:
            kind = element["type"] or "UNKNOWN"
            type_counts[kind] = type_counts.get(kind, 0) + 1
        layouts.append(
            {
                "name": layout.name,
                "page_width": number(layout.pageWidth),
                "page_height": number(layout.pageHeight),
                "element_count": len(elements),
                "element_type_counts": type_counts,
                "map_surround_signature": surrounds,
            }
        )

    report = {
        "project": project_path,
        "maps_count": len(maps),
        "layouts_count": len(layouts),
        "layers_count": sum(len(item["layers"]) for item in maps),
        "tables_count": sum(len(item["tables"]) for item in maps),
        "broken_count": len(broken),
        "broken": broken,
        "data_sources": sorted(all_sources, key=os.path.normcase),
        "maps": maps,
        "layouts": layouts,
    }
    del project
    gc.collect()
    return report


def write_new(path: str, text: str) -> None:
    output = Path(path).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("--output", help="Optional new JSON report path. Must not exist.")
    args = parser.parse_args()

    report = inspect_project(args.project)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if args.output:
        write_new(args.output, text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
