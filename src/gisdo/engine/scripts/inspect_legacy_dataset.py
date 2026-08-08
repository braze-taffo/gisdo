#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Read-only ArcMap dataset, field, CRS, extent, and lock diagnosis."""

from __future__ import print_function

import argparse
import glob
import json
import os
import sys

import arcpy


PY2 = sys.version_info[0] < 3


def text(value):
    if value is None:
        return None
    if isinstance(value, bytes):  # Py2 str / Py3 bytes，可能是 UTF-8 或本地码页（GBK）路径
        for enc in ("utf-8", "gbk", "mbcs"):
            try:
                return value.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return value.decode("utf-8", "replace")
    try:
        return unicode(value)  # noqa: F821 - Python 2
    except NameError:
        return str(value)


def emit(record):
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
    if PY2:
        sys.stdout.write(payload.encode("utf-8"))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(payload + "\n")


def cursor_target(path):
    if path.lower().endswith(".shp"):
        arcpy.env.workspace = os.path.dirname(path)
        return os.path.basename(path)
    return path


def lock_files(path):
    lowered = path.lower()
    gdb_index = lowered.find(".gdb")
    if gdb_index >= 0:
        root = path[: gdb_index + 4]
        return sorted(glob.glob(os.path.join(root, "*.lock")))
    if path.lower().endswith(".shp"):
        stem = os.path.splitext(os.path.basename(path))[0]
        parent = os.path.dirname(path)
        return sorted(glob.glob(os.path.join(parent, stem + "*.lock")))
    return []


def extent_record(extent):
    if extent is None:
        return None
    return {
        "xmin": getattr(extent, "XMin", None),
        "ymin": getattr(extent, "YMin", None),
        "xmax": getattr(extent, "XMax", None),
        "ymax": getattr(extent, "YMax", None),
    }


def spatial_reference_record(spatial_reference):
    if spatial_reference is None:
        return None
    return {
        "name": text(getattr(spatial_reference, "name", None)),
        "type": text(getattr(spatial_reference, "type", None)),
        "factory_code": getattr(spatial_reference, "factoryCode", None),
        "linear_unit": text(getattr(spatial_reference, "linearUnitName", None)),
        "angular_unit": text(getattr(spatial_reference, "angularUnitName", None)),
    }


def inspect_dataset(dataset):
    path = os.path.abspath(dataset)
    target = cursor_target(path)
    recognized = bool(arcpy.Exists(target))
    report = {
        "dataset": text(path),
        "filesystem_exists": os.path.exists(path),
        "arcpy_exists": recognized,
        "workspace": text(getattr(arcpy.env, "workspace", None)),
        "cursor_target": text(target),
        "lock_files": [text(item) for item in lock_files(path)],
        "schema_lock_available": None,
        "cursor_probe": None,
        "errors": [],
    }
    if not recognized:
        report["errors"].append(
            "ArcPy does not recognize the dataset. Check the path, sidecars, runtime, and writer locks."
        )
        return report

    try:
        if hasattr(arcpy, "TestSchemaLock"):
            report["schema_lock_available"] = bool(arcpy.TestSchemaLock(target))
    except Exception as exc:
        report["errors"].append("TestSchemaLock failed: " + text(exc))

    try:
        desc = arcpy.Describe(target)
        fields = []
        for field in arcpy.ListFields(target) or []:
            fields.append(
                {
                    "name": text(field.name),
                    "alias": text(getattr(field, "aliasName", None)),
                    "type": text(field.type),
                    "length": getattr(field, "length", None),
                    "required": bool(getattr(field, "required", False)),
                }
            )
        report.update(
            {
                "catalog_path": text(getattr(desc, "catalogPath", None)),
                "data_type": text(getattr(desc, "dataType", None)),
                "shape_type": text(getattr(desc, "shapeType", None)),
                "has_oid": bool(getattr(desc, "hasOID", False)),
                "oid_field": text(getattr(desc, "OIDFieldName", None)),
                "extent": extent_record(getattr(desc, "extent", None)),
                "spatial_reference": spatial_reference_record(
                    getattr(desc, "spatialReference", None)
                ),
                "fields": fields,
                "numeric_fields": [
                    item["name"]
                    for item in fields
                    if item["type"] in ("SmallInteger", "Integer", "Single", "Double")
                ],
                "count": int(arcpy.management.GetCount(target).getOutput(0)),
            }
        )
    except Exception as exc:
        report["errors"].append("Describe/field/count inspection failed: " + text(exc))

    try:
        with arcpy.da.SearchCursor(target, ["OID@"]) as rows:
            first = next(rows, None)
        report["cursor_probe"] = {"readable": True, "first_oid": first[0] if first else None}
    except Exception as exc:
        report["cursor_probe"] = {"readable": False, "error": text(exc)}
        report["errors"].append("SearchCursor failed; check active ArcMap locks first.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    args = parser.parse_args()
    report = inspect_dataset(args.dataset)
    emit(report)
    return 0 if report["arcpy_exists"] and not report["errors"] else 3


if __name__ == "__main__":
    sys.exit(main())
