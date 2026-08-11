# -*- coding: utf-8 -*-
from __future__ import print_function

"""GISdo ArcPy JSONL Worker core (Python 2.7/3.x compatible).

Stdout is protocol-only. Diagnostics go to stderr. The Rust supervisor serializes
requests per process and kills the process for hard cancellation.
"""

import gc
import glob
import io
import json
import os
import platform
import struct
import sys
import time
import traceback


PROTOCOL_VERSION = 1
ALLOWED_TOOLBOXES = ("management", "analysis", "conversion")
CUSTOM_TOOLS = (
    "inspect_aprx", "inspect_mxd", "inspect_gdb", "package_project", "verify_png"
)

DISCOVERY_EXTENSIONS = (
    ".shp", ".geojson", ".json", ".gpkg", ".sqlite", ".dbf", ".csv",
    ".tif", ".tiff", ".img", ".jp2", ".crf", ".las", ".laz",
    ".dwg", ".dxf", ".dgn", ".aprx", ".mxd", ".lyr", ".lyrx",
    ".kml", ".kmz",
)


class ProtocolError(Exception):
    pass


class FakeParameter(object):
    def __init__(self, name, direction, required, datatype):
        self.name = name
        self.direction = direction
        self.parameterType = "Required" if required else "Optional"
        self.datatype = datatype


class FakeResult(object):
    def __init__(self, output):
        self.output = output

    def getOutput(self, _index):
        return self.output


class FakeToolbox(object):
    def __getattr__(self, _name):
        def run(**params):
            output = None
            for key in sorted(params):
                if key.startswith("out_") or key in ("output", "output_file"):
                    output = params[key]
            if output:
                parent = os.path.dirname(output)
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent)
                with io.open(output, "w", encoding="utf-8") as handle:
                    handle.write(u"fake arcpy result")
            return FakeResult(output or "")
        return run


class FakeArcpy(object):
    class _Env(object):
        overwriteOutput = False
        workspace = None
        scratchWorkspace = None
    env = _Env()
    management = FakeToolbox()
    analysis = FakeToolbox()
    conversion = FakeToolbox()

    @staticmethod
    def GetParameterInfo(_full_name):
        return [
            FakeParameter("in_features", "Input", True, "DEFeatureClass"),
            FakeParameter("out_feature_class", "Output", True, "DEFeatureClass"),
        ]

    @staticmethod
    def GetInstallInfo():
        return {"Version": "fake"}

    @staticmethod
    def GetMessages():
        return "fake complete"

    @staticmethod
    def Exists(path):
        return os.path.exists(path)

    @staticmethod
    def Describe(path):
        class Description(object):
            catalogPath = path
            extent = None
            spatialReference = None
        return Description()

    @staticmethod
    def ClearWorkspaceCache_management():
        return None


def load_arcpy():
    if os.environ.get("GISDO_WORKER_FAKE_ARCPY") == "1":
        return FakeArcpy()
    import arcpy
    return arcpy


def stderr(message):
    data = (u"[gisdo-worker] " + to_text(message) + u"\n").encode("utf-8", "replace")
    stream = getattr(sys.stderr, "buffer", sys.stderr)
    stream.write(data)
    stream.flush()


def to_text(value):
    if isinstance(value, type(u"")):
        return value
    try:
        return value.decode("utf-8", "replace")
    except AttributeError:
        return type(u"")(value)


def emit(record):
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    if not isinstance(payload, bytes):
        payload = payload.encode("utf-8")
    stream = getattr(sys.stdout, "buffer", sys.stdout)
    stream.write(payload + b"\n")
    stream.flush()


def read_request():
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    line = stream.readline()
    if not line:
        return None
    if isinstance(line, bytes):
        line = line.decode("utf-8")
    value = json.loads(line)
    if not isinstance(value, dict) or not value.get("type"):
        raise ProtocolError("request must be an object with type")
    return value


def parameter_schema(arcpy, toolbox, tool_name):
    full_name = "%s_%s" % (tool_name, toolbox)
    schema = []
    for parameter in arcpy.GetParameterInfo(full_name) or []:
        name = getattr(parameter, "name", None)
        if not name:
            continue
        schema.append({
            "name": name,
            "direction": getattr(parameter, "direction", None),
            "required": getattr(parameter, "parameterType", None) == "Required",
            "datatype": getattr(parameter, "datatype", None),
        })
    return schema


def looks_like_dataset_type(datatype):
    text = to_text(datatype or "").lower()
    return any(marker in text for marker in (
        "defile", "defolder", "deworkspace", "defeature", "deraster", "detable",
        "gplayer", "gpfeature", "gpraster", "gptable", "cad", "tin", "terrain",
        u"文件", u"文件夹", u"工作空间", u"要素", u"栅格", u"表", u"图层", u"数据集"
    ))


def values(value):
    if isinstance(value, list):
        return value
    return [value]


def validate_input_compatibility(arcpy, path, datatype):
    """Reject containers where a geoprocessing parameter requires a dataset."""
    text = to_text(datatype or "").lower()
    requires_feature = "feature" in text or u"要素" in text
    requires_raster = "raster" in text or u"栅格" in text
    requires_table = "table" in text or u"表" in text
    if not (requires_feature or requires_raster or requires_table):
        return
    if os.path.isdir(path):
        raise ProtocolError(
            "input parameter requires a dataset, but a folder/workspace was provided: %s" % path
        )
    try:
        description = arcpy.Describe(path)
    except Exception as error:
        raise ProtocolError("cannot describe input dataset %s: %s" % (path, error))
    if requires_feature and not getattr(description, "shapeType", None):
        raise ProtocolError("input parameter requires a feature dataset: %s" % path)
    if requires_raster:
        actual = to_text(getattr(description, "dataType", "") or getattr(description, "datasetType", "")).lower()
        if "raster" not in actual and u"栅格" not in actual:
            raise ProtocolError("input parameter requires a raster dataset: %s" % path)


def validate_official_step(arcpy, step):
    tool = step.get("tool") or ""
    if tool.lower() in ("getcount", "management.getcount"):
        raise ProtocolError("GetCount is redundant; validation is automatic")
    if "." not in tool:
        raise ProtocolError("official tool must be toolbox.tool")
    toolbox, tool_name = tool.split(".", 1)
    if toolbox not in ALLOWED_TOOLBOXES:
        raise ProtocolError("toolbox is not allowed: %s" % toolbox)
    params = step.get("params") or {}
    if not isinstance(params, dict):
        raise ProtocolError("params must be an object")
    schema = parameter_schema(arcpy, toolbox, tool_name)
    known = set(item["name"] for item in schema)
    unknown = sorted(set(params) - known)
    if unknown:
        raise ProtocolError("unknown parameters for %s: %s" % (tool, ", ".join(unknown)))
    missing = [item["name"] for item in schema if item["required"] and item["name"] not in params]
    if missing:
        raise ProtocolError("missing required parameters for %s: %s" % (tool, ", ".join(missing)))
    inputs = []
    outputs = []
    for item in schema:
        if item["name"] not in params or not looks_like_dataset_type(item["datatype"]):
            continue
        targets = [to_text(item) for item in values(params[item["name"]]) if isinstance(item, (bytes, type(u"")))]
        if item["direction"] == "Output":
            outputs.extend(targets)
        elif item["direction"] == "Input":
            inputs.extend(targets)
    input_types = {}
    for item in schema:
        if item["name"] not in params or item["direction"] != "Input":
            continue
        for target in values(params[item["name"]]):
            if isinstance(target, (bytes, type(u""))):
                input_types[to_text(target)] = item["datatype"]
    for path in inputs:
        if path and not arcpy.Exists(path) and not os.path.exists(path):
            raise ProtocolError("input does not exist: %s" % path)
        validate_input_compatibility(arcpy, path, input_types.get(path))
    for path in outputs:
        if not os.path.isabs(path):
            raise ProtocolError("output must be absolute: %s" % path)
        if arcpy.Exists(path) or os.path.exists(path):
            raise ProtocolError("output already exists: %s" % path)
    validate_free_space(inputs, outputs)
    return toolbox, tool_name, coerce_official_params(arcpy, schema, params), outputs


def dataset_bytes(path):
    if os.path.isfile(path):
        stem, extension = os.path.splitext(path)
        if extension.lower() == ".shp":
            return sum(os.path.getsize(item) for item in glob.glob(stem + ".*") if os.path.isfile(item))
        return os.path.getsize(path)
    if os.path.isdir(path):
        total = 0
        for root, _dirs, names in os.walk(path):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total
    return 0


def nearest_existing_parent(path):
    candidate = os.path.abspath(os.path.dirname(path) or path)
    while not os.path.exists(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return candidate


def free_bytes(path):
    root = nearest_existing_parent(path)
    if sys.platform == "win32":
        import ctypes
        available = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        free = ctypes.c_ulonglong(0)
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            to_text(root), ctypes.byref(available), ctypes.byref(total), ctypes.byref(free)
        )
        if not ok:
            raise ctypes.WinError()
        return available.value
    stats = os.statvfs(root)
    return stats.f_bavail * stats.f_frsize


def validate_free_space(inputs, outputs):
    if not outputs:
        return
    input_bytes = sum(dataset_bytes(path) for path in inputs)
    required = max(64 * 1024 * 1024, int(input_bytes * 1.10) + 64 * 1024 * 1024)
    checked_roots = set()
    for output in outputs:
        root = os.path.splitdrive(os.path.abspath(output))[0].lower()
        if root in checked_roots:
            continue
        checked_roots.add(root)
        available = free_bytes(output)
        if available < required:
            raise ProtocolError(
                "insufficient free space for output: available=%s required=%s path=%s"
                % (available, required, output)
            )


def ensure_output_parents(outputs):
    for path in outputs:
        parent = os.path.dirname(path)
        if not parent or os.path.isdir(parent):
            continue
        lower = parent.lower()
        if lower.endswith((".gdb", ".mdb", ".sde")):
            raise ProtocolError("output workspace does not exist: %s" % parent)
        os.makedirs(parent)


def coerce_official_params(arcpy, schema, params):
    converted = dict(params)
    for item in schema:
        name = item["name"]
        if name not in converted:
            continue
        datatype = to_text(item.get("datatype") or "").lower()
        if u"坐标系" not in datatype and "coordinate system" not in datatype and "spatial reference" not in datatype:
            continue
        value = converted[name]
        if isinstance(value, (bytes, type(u""))):
            text = to_text(value).strip()
            if text.lower().startswith("epsg:"):
                text = text.split(":", 1)[1]
            if text.isdigit():
                value = int(text)
        if isinstance(value, int):
            converted[name] = arcpy.SpatialReference(value)
    return converted


def extent_json(extent):
    if extent is None:
        return None
    try:
        return {
            "xmin": extent.XMin, "ymin": extent.YMin,
            "xmax": extent.XMax, "ymax": extent.YMax,
        }
    except Exception:
        return None


def role_hints(path, shape_type, count):
    name = to_text(path).lower()
    hints = []
    if any(marker in name for marker in (u"边界", u"区界", u"行政区", u"范围", "boundary", "border", "mask", "clip")):
        hints.append("likely_clip_boundary")
    if any(marker in name for marker in (u"建筑", u"房屋", "building", "footprint")):
        hints.append("likely_primary_input")
    if to_text(shape_type or "").lower() == "polygon" and count is not None and count <= 100:
        hints.append("small_polygon_boundary_candidate")
    return hints


def describe_dataset(arcpy, path):
    item = {"path": path, "name": os.path.basename(path), "exists": bool(arcpy.Exists(path) or os.path.exists(path))}
    try:
        description = arcpy.Describe(path)
        item["data_type"] = to_text(getattr(description, "dataType", "") or "")
        item["dataset_type"] = to_text(getattr(description, "datasetType", "") or "")
        item["shape_type"] = to_text(getattr(description, "shapeType", "") or "") or None
        item["extent"] = extent_json(getattr(description, "extent", None))
        spatial_reference = getattr(description, "spatialReference", None)
        if spatial_reference is not None:
            item["spatial_reference"] = {
                "name": to_text(getattr(spatial_reference, "name", "") or ""),
                "factory_code": getattr(spatial_reference, "factoryCode", None),
                "type": to_text(getattr(spatial_reference, "type", "") or ""),
            }
        count = None
        try:
            count = int(arcpy.management.GetCount(path).getOutput(0))
        except Exception:
            try:
                count = int(arcpy.GetCount_management(path).getOutput(0))
            except Exception:
                pass
        item["count"] = count
        try:
            item["fields"] = [to_text(field.name) for field in arcpy.ListFields(path)][:40]
        except Exception:
            item["fields"] = []
        item["role_hints"] = role_hints(path, item.get("shape_type"), count)
        shape_type = to_text(item.get("shape_type") or "").lower()
        kind = (to_text(item.get("data_type") or "") + " " + to_text(item.get("dataset_type") or "")).lower()
        is_spatial = item.get("extent") is not None and item.get("spatial_reference") is not None
        has_geometry = shape_type not in ("", "null")
        is_raster = "raster" in kind or u"栅格" in kind
        is_table = "table" in kind or "text" in kind or u"表" in kind
        item["usable"] = bool((is_spatial and (has_geometry or is_raster)) or (is_table and count is not None))
        if not item["usable"]:
            item["unusable_reason"] = "missing usable geometry, extent, or spatial reference"
    except Exception as error:
        item["usable"] = False
        item["inspection_error"] = "%s: %s" % (type(error).__name__, to_text(error))
    return item


def discover_paths(arcpy, roots, max_items):
    candidates = []
    seen = set()
    truncated = False

    def add(path):
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized in seen:
            return True
        seen.add(normalized)
        candidates.append(os.path.abspath(path))
        return len(candidates) < max_items

    for original in roots:
        root = os.path.abspath(to_text(original))
        if not os.path.exists(root) and not arcpy.Exists(root):
            if add(root):
                continue
            truncated = True
            break
        if os.path.isfile(root) or not os.path.isdir(root):
            if not add(root):
                truncated = True
                break
            continue
        if root.lower().endswith((".gdb", ".mdb", ".sde")):
            try:
                for walk_root, _dirs, names in arcpy.da.Walk(root):
                    for name in names:
                        if not add(os.path.join(walk_root, name)):
                            truncated = True
                            break
                    if truncated:
                        break
            except Exception as error:
                candidates.append({"path": root, "inspection_error": to_text(error)})
            if truncated:
                break
            continue
        for walk_root, dirs, names in os.walk(root):
            workspace_dirs = [name for name in dirs if name.lower().endswith((".gdb", ".mdb"))]
            dirs[:] = [name for name in dirs if name not in workspace_dirs]
            for workspace in workspace_dirs:
                workspace_path = os.path.join(walk_root, workspace)
                try:
                    for data_root, _data_dirs, data_names in arcpy.da.Walk(workspace_path):
                        for name in data_names:
                            if not add(os.path.join(data_root, name)):
                                truncated = True
                                break
                        if truncated:
                            break
                except Exception:
                    add(workspace_path)
                if truncated:
                    break
            if truncated:
                break
            for name in names:
                extension = os.path.splitext(name)[1].lower()
                if extension == ".dbf" and os.path.isfile(os.path.join(walk_root, os.path.splitext(name)[0] + ".shp")):
                    continue
                if extension in DISCOVERY_EXTENSIONS:
                    if not add(os.path.join(walk_root, name)):
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break

    datasets = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            datasets.append(candidate)
        else:
            datasets.append(describe_dataset(arcpy, candidate))
    return {
        "roots": [to_text(path) for path in roots],
        "datasets": datasets,
        "dataset_count": len(datasets),
        "truncated": truncated,
    }


def validate_output(arcpy, path, policy):
    record = {"path": path, "exists": bool(arcpy.Exists(path) or os.path.exists(path))}
    if not record["exists"]:
        raise RuntimeError("declared output was not created: %s" % path)
    if policy == "none":
        return record
    if policy == "png":
        record.update(validate_png(path))
        return record
    try:
        description = arcpy.Describe(path)
        spatial_reference = getattr(description, "spatialReference", None)
        if spatial_reference is not None:
            record["spatial_reference"] = {
                "name": getattr(spatial_reference, "name", None),
                "factory_code": getattr(spatial_reference, "factoryCode", None),
            }
        record["extent"] = extent_json(getattr(description, "extent", None))
        try:
            record["count"] = int(arcpy.management.GetCount(path).getOutput(0))
        except Exception:
            try:
                record["count"] = int(arcpy.GetCount_management(path).getOutput(0))
            except Exception:
                pass
    except Exception as error:
        record["describe_warning"] = "%s: %s" % (type(error).__name__, error)
    return record


def validate_png(path):
    with io.open(path, "rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("not a valid PNG: %s" % path)
    width, height = struct.unpack(">II", header[16:24])
    if width < 16 or height < 16:
        raise RuntimeError("PNG dimensions too small: %sx%s" % (width, height))
    return {"width": width, "height": height, "bytes": os.path.getsize(path)}


def inspect_aprx(arcpy, path):
    if not os.path.isfile(path):
        raise ProtocolError("project does not exist: %s" % path)
    project = arcpy.mp.ArcGISProject(path)
    try:
        maps = project.listMaps()
        layouts = project.listLayouts()
        broken = []
        for map_item in maps:
            for layer in map_item.listLayers():
                if getattr(layer, "isBroken", False):
                    broken.append({"map": map_item.name, "layer": layer.name})
        return {"project": path, "maps_count": len(maps), "layouts_count": len(layouts), "broken_count": len(broken), "broken": broken}
    finally:
        del project


def inspect_mxd(arcpy, path):
    if not os.path.isfile(path):
        raise ProtocolError("project does not exist: %s" % path)
    project = arcpy.mapping.MapDocument(path)
    try:
        frames = arcpy.mapping.ListDataFrames(project)
        broken = arcpy.mapping.ListBrokenDataSources(project)
        return {"project": path, "data_frames_count": len(frames), "broken_count": len(broken)}
    finally:
        del project


def run_custom(arcpy, runtime_kind, step):
    tool = step.get("tool")
    params = step.get("params") or {}
    if tool == "inspect_aprx":
        if runtime_kind != "pro":
            raise ProtocolError("inspect_aprx requires pro runtime")
        return inspect_aprx(arcpy, params["project"]), []
    if tool == "inspect_mxd":
        if runtime_kind != "arcmap":
            raise ProtocolError("inspect_mxd requires arcmap runtime")
        return inspect_mxd(arcpy, params["project"]), []
    if tool == "inspect_gdb":
        workspace = params["workspace"]
        if not arcpy.Exists(workspace):
            raise ProtocolError("workspace does not exist: %s" % workspace)
        datasets = []
        for root, _dirs, names in arcpy.da.Walk(workspace):
            for name in names:
                datasets.append(os.path.join(root, name))
        return {"workspace": workspace, "dataset_count": len(datasets), "datasets": datasets}, []
    if tool == "package_project":
        output = params["output"]
        if os.path.exists(output):
            raise ProtocolError("output already exists: %s" % output)
        arcpy.management.PackageProject(params["project"], output)
        return {"validation": validate_output(arcpy, output, "package")}, [output]
    if tool == "verify_png":
        return validate_png(params["input"]), []
    raise ProtocolError("custom tool is not implemented by this worker: %s" % tool)


def cleanup_arcpy(arcpy):
    try:
        arcpy.env.workspace = None
        arcpy.env.scratchWorkspace = None
        try:
            arcpy.management.ClearWorkspaceCache()
        except Exception:
            arcpy.ClearWorkspaceCache_management()
    except Exception as error:
        stderr("workspace cleanup warning: %s" % error)
    gc.collect()


def execute_step(arcpy, runtime_kind, request_id, step):
    step_id = step.get("id") or "unknown"
    emit({"type": "step_started", "request_id": request_id, "step_id": step_id})
    started = time.time()
    write_started = False
    artifacts = []
    try:
        if step.get("tool") in CUSTOM_TOOLS:
            write_started = step.get("tool") in ("package_project",)
            result, artifacts = run_custom(arcpy, runtime_kind, step)
        else:
            toolbox, tool_name, params, outputs = validate_official_step(arcpy, step)
            emit({"type": "progress", "request_id": request_id, "step_id": step_id, "percent": 5.0, "message": "validation complete"})
            arcpy.env.overwriteOutput = False
            write_started = bool(outputs)
            ensure_output_parents(outputs)
            function = getattr(getattr(arcpy, toolbox), tool_name)
            tool_result = function(**params)
            emit({"type": "progress", "request_id": request_id, "step_id": step_id, "percent": 85.0, "message": to_text(arcpy.GetMessages())})
            validations = [validate_output(arcpy, path, step.get("validation") or "dataset") for path in outputs]
            artifacts = outputs
            result = {"tool": step.get("tool"), "messages": to_text(arcpy.GetMessages()), "outputs": validations}
            del tool_result
        result["duration_ms"] = int((time.time() - started) * 1000)
        emit({"type": "step_completed", "request_id": request_id, "step_id": step_id, "result": result, "artifacts": artifacts})
        return result
    except Exception as error:
        emit({
            "type": "error", "request_id": request_id, "step_id": step_id,
            "code": type(error).__name__, "message": to_text(error),
            "write_started": write_started,
            "severe": not isinstance(error, (ProtocolError, ValueError)),
        })
        raise
    finally:
        cleanup_arcpy(arcpy)


def serve(runtime_kind):
    arcpy = load_arcpy()
    hello = read_request()
    if hello is None or hello.get("type") != "hello" or hello.get("protocol") != PROTOCOL_VERSION:
        raise ProtocolError("first request must be hello protocol 1")
    try:
        install = arcpy.GetInstallInfo() or {}
        arcpy_version = install.get("Version")
    except Exception:
        arcpy_version = None
    emit({
        "type": "ready", "protocol": PROTOCOL_VERSION, "runtime": runtime_kind,
        "python_version": platform.python_version(), "arcpy_version": arcpy_version,
        "pid": os.getpid(),
    })
    while True:
        request = read_request()
        if request is None:
            return 0
        if request.get("type") == "shutdown":
            emit({"type": "stopped"})
            return 0
        if request.get("type") == "inspect_paths":
            request_id = request.get("request_id")
            roots = request.get("roots") or []
            try:
                if not request_id or not isinstance(roots, list):
                    raise ProtocolError("inspect_paths requires request_id and roots")
                max_items = max(1, min(int(request.get("max_items") or 200), 1000))
                inventory = discover_paths(arcpy, roots, max_items)
                emit({"type": "inspection_completed", "request_id": request_id, "inventory": inventory})
            except Exception as error:
                emit({
                    "type": "error", "request_id": request_id, "step_id": None,
                    "code": type(error).__name__, "message": to_text(error),
                    "write_started": False, "severe": False,
                })
            finally:
                cleanup_arcpy(arcpy)
            continue
        if request.get("type") != "execute_plan":
            emit({"type": "error", "request_id": request.get("request_id"), "step_id": None, "code": "protocol", "message": "unsupported request", "write_started": False, "severe": False})
            continue
        request_id = request.get("request_id")
        results = []
        try:
            if not request.get("plan_hash"):
                raise ProtocolError("plan_hash is required")
            for step in request.get("steps") or []:
                results.append(execute_step(arcpy, runtime_kind, request_id, step))
            emit({"type": "plan_completed", "request_id": request_id, "results": results})
        except Exception:
            # execute_step already emitted a structured error; keep process alive for
            # expected validation failures. Rust recycles it after severe errors.
            stderr(traceback.format_exc())


def main(runtime_kind):
    try:
        return serve(runtime_kind)
    except Exception as error:
        stderr(traceback.format_exc())
        try:
            emit({"type": "error", "request_id": None, "step_id": None, "code": type(error).__name__, "message": to_text(error), "write_started": False, "severe": True})
        except Exception:
            pass
        return 1
