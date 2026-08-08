#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Export ArcMap-readable polylines and one numeric field to portable JSON."""

from __future__ import print_function

import argparse
import json
import math
import os
import sys

import arcpy


PY2 = sys.version_info[0] < 3
NUMERIC_TYPES = ("SmallInteger", "Integer", "Single", "Double")


def text(value):
    if value is None:
        return None
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


def finite_number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def spatial_reference_record(spatial_reference):
    return {
        "name": text(getattr(spatial_reference, "name", None)),
        "type": text(getattr(spatial_reference, "type", None)),
        "factory_code": getattr(spatial_reference, "factoryCode", None),
        "linear_unit": text(getattr(spatial_reference, "linearUnitName", None)),
        "angular_unit": text(getattr(spatial_reference, "angularUnitName", None)),
    }


def geometry_parts(geometry):
    result = []
    for part in geometry:
        current = []
        for point in part:
            if point is None:
                if len(current) >= 2:
                    result.append(current)
                current = []
            else:
                current.append([float(point.X), float(point.Y)])
        if len(current) >= 2:
            result.append(current)
    return result


def write_new_json(path, record):
    output = os.path.abspath(path)
    if os.path.exists(output):
        raise IOError("Refusing to overwrite: " + output)
    parent = os.path.dirname(output)
    if not os.path.isdir(parent):
        raise IOError("Output directory does not exist: " + parent)
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
    if not isinstance(payload, bytes):
        payload = payload.encode("utf-8")
    with open(output, "wb") as handle:
        handle.write(payload)
        handle.write(b"\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("value_field")
    parser.add_argument("output_json", help="New JSON file; must not exist.")
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Skip null/non-numeric values or empty geometry and record their OIDs.",
    )
    args = parser.parse_args()

    path = os.path.abspath(args.dataset)
    target = cursor_target(path)
    output = os.path.abspath(args.output_json)
    if os.path.exists(output):
        raise IOError("Refusing to overwrite: " + output)
    if not arcpy.Exists(target):
        raise IOError("ArcPy cannot open the dataset; inspect paths and locks first: " + path)

    desc = arcpy.Describe(target)
    if text(getattr(desc, "shapeType", "")).lower() != "polyline":
        raise ValueError("Expected Polyline geometry, found: " + text(desc.shapeType))

    fields = {text(field.name).lower(): field for field in arcpy.ListFields(target) or []}
    requested = text(args.value_field).lower()
    if requested not in fields:
        raise ValueError("Field not found: " + text(args.value_field))
    field = fields[requested]
    if text(field.type) not in NUMERIC_TYPES:
        raise ValueError("Classification field must be numeric, found: " + text(field.type))

    features = []
    invalid = []
    values = []
    part_count = 0
    with arcpy.da.SearchCursor(target, ["OID@", field.name, "SHAPE@"]) as rows:
        for oid, raw_value, geometry in rows:
            value = finite_number(raw_value)
            parts = geometry_parts(geometry) if geometry else []
            if value is None or not parts:
                invalid.append(
                    {"oid": oid, "reason": "invalid_value" if value is None else "empty_geometry"}
                )
                continue
            features.append({"oid": oid, "value": value, "parts": parts})
            values.append(value)
            part_count += len(parts)

    if invalid and not args.skip_invalid:
        raise ValueError(
            "Found {0} invalid feature(s); rerun only with --skip-invalid after review. OIDs: {1}".format(
                len(invalid), ", ".join(text(item["oid"]) for item in invalid[:20])
            )
        )
    if not features:
        raise ValueError("No valid line features were exported.")

    extent = getattr(desc, "extent", None)
    record = {
        "format": "gisdo-classified-lines",
        "version": 1,
        "source": path,
        "value_field": text(field.name),
        "source_count": int(arcpy.management.GetCount(target).getOutput(0)),
        "exported_feature_count": len(features),
        "exported_part_count": part_count,
        "value_min": min(values),
        "value_max": max(values),
        "invalid_count": len(invalid),
        "invalid_features": invalid,
        "extent": {
            "xmin": getattr(extent, "XMin", None),
            "ymin": getattr(extent, "YMin", None),
            "xmax": getattr(extent, "XMax", None),
            "ymax": getattr(extent, "YMax", None),
        },
        "spatial_reference": spatial_reference_record(
            getattr(desc, "spatialReference", None)
        ),
        "features": features,
    }
    write_new_json(output, record)
    emit(
        {
            "output_json": output,
            "source_count": record["source_count"],
            "exported_feature_count": record["exported_feature_count"],
            "exported_part_count": record["exported_part_count"],
            "value_field": record["value_field"],
            "value_min": record["value_min"],
            "value_max": record["value_max"],
            "invalid_count": record["invalid_count"],
            "spatial_reference": record["spatial_reference"],
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
