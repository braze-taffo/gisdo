#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Read-only ArcMap MXD inventory; compatible with ArcGIS Desktop Python 2.7."""

from __future__ import print_function

import argparse
import json
import os
import sys

import arcpy


def text(value):
    if value is None:
        return None
    try:
        return unicode(value)  # noqa: F821 - Python 2
    except NameError:
        return str(value)


def inspect_mxd(path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise IOError("MXD not found: " + path)
    if not hasattr(arcpy, "mapping"):
        raise RuntimeError("arcpy.mapping is unavailable; run with the ArcMap Python runtime")

    document = arcpy.mapping.MapDocument(path)
    frames = []
    sources = []
    for frame in arcpy.mapping.ListDataFrames(document):
        layers = []
        for layer in arcpy.mapping.ListLayers(document, "", frame):
            source = None
            try:
                source = text(layer.dataSource)
            except Exception:
                pass
            if source:
                sources.append(source)
            layers.append(
                {
                    "name": text(layer.name),
                    "long_name": text(getattr(layer, "longName", None)),
                    "data_source": source,
                    "is_broken": bool(getattr(layer, "isBroken", False)),
                }
            )
        frames.append({"name": text(frame.name), "layers": layers})

    broken = []
    for item in arcpy.mapping.ListBrokenDataSources(document):
        broken.append(
            {
                "name": text(getattr(item, "name", None)),
                "data_source": text(getattr(item, "dataSource", None)),
            }
        )
    report = {
        "project": path,
        "data_frame_count": len(frames),
        "layer_count": sum(len(frame["layers"]) for frame in frames),
        "broken_count": len(broken),
        "broken": broken,
        "data_sources": sorted(set(sources)),
        "data_frames": frames,
    }
    del document
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    args = parser.parse_args()
    output = json.dumps(inspect_mxd(args.project), ensure_ascii=False, indent=2)
    if sys.version_info[0] < 3:
        output = output.encode("utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
