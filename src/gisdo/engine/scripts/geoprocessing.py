#!/usr/bin/env python
"""Generic safe wrapper around official ArcPy geoprocessing tools.

Runs one whitelisted official tool (management / analysis / conversion) with
``arcpy.env.overwriteOutput = False``, refuses pre-existing outputs, verifies
path-like inputs exist, and reports the output's describe / count / extent as
proportional validation.  Emits a single JSON record last on stdout, so the app
side can extract it even when ArcPy interleaves GP messages.

Exit codes follow the repo convention: ``0`` success, ``3`` validation failed
(tool reported success but the output is missing), ``1`` runtime error.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import time

import arcpy

VALIDATION_FAILED_RC = 3
ALLOWED_TOOLBOXES = ("management", "analysis", "conversion")


def parse_tool(tool: str) -> tuple[str, str]:
    parts = tool.split(".")
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise ValueError(
            f"工具名必须是 toolbox.tool 全名（如 analysis.Clip、management.Project），收到 {tool!r}"
        )
    toolbox, name = parts
    if toolbox not in ALLOWED_TOOLBOXES:
        raise ValueError(f"工具箱 {toolbox!r} 不在白名单（允许：{'、'.join(ALLOWED_TOOLBOXES)}）")
    return toolbox, name


def looks_like_path(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    low = value.lower()
    if os.sep in value or "\\" in value or "/" in value:
        return True
    return low.endswith(
        (".gdb", ".shp", ".aprx", ".tif", ".tiff", ".img", ".dbf", ".mxd", ".ppkx", ".gpkg")
    )


def find_output_param(info, explicit: str | None):
    """主输出参数：显式指定优先，但若名字不在工具参数中则回退自动识别（LLM 可能猜错参数名）。"""
    if explicit:
        by_name = {p.name: p for p in info if p.name}
        if explicit in by_name:
            return by_name[explicit]
    for p in info:
        if p.direction == "Output" and p.name:
            return p
    raise ValueError("该工具没有可识别的主输出参数，请显式提供 output_param。")


def build_values(info, params: dict, output: str, out_param_name: str):
    """按工具签名把 params 排成位置参数列表；派生输出与无名参数跳过。"""
    values: list = []
    missing: list[str] = []
    kept: list = []
    for p in info:
        name = p.name
        if p.direction == "Derived" or not name:
            continue
        kept.append(p)
        if name == out_param_name:
            values.append(output)
        elif name in params:
            values.append(params[name])
        elif p.parameterType == "Required" and p.direction != "Output":
            missing.append(name)
            values.append(None)
        else:
            values.append(None)
    return values, missing, kept


def missing_inputs(kept, values) -> list[str]:
    """路径样输入必须存在（arcpy.Exists），给出友好错误而非 arcpy 深坑。"""
    bad: list[str] = []
    for p, value in zip(kept, values):
        if p.direction != "Input" or value is None:
            continue
        if isinstance(value, str) and looks_like_path(value) and not arcpy.Exists(value):
            bad.append(value)
    return bad


def ensure_output_parent(output: str) -> None:
    """为文件类输出创建父目录；GDB 内输出不预建（避免造出假 .gdb 目录）。"""
    if ".gdb" in output.lower():
        return
    parent = os.path.dirname(output)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


def resolve_tool(toolbox: str, name: str):
    mod = getattr(arcpy, toolbox, None)
    func = getattr(mod, name, None) if mod is not None else None
    if func is None:
        func = getattr(arcpy, f"{name}_{toolbox}", None)
    if func is None:
        raise ValueError(f"工具箱 {toolbox} 中没有工具 {name}")
    return func


def _positional_params(func) -> list[str] | None:
    """arcpy 函数可接受的位置参数名；无法过滤时返回 None（保守全传）。

    签名含 ``*args`` 时无法按名过滤，返回 None；builtin 或签名解析失败同理。
    """
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return None
    params = list(sig.parameters.values())
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params):
        return None
    return [p.name for p in params
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]


def call_tool(func, kept: list, values: list) -> None:
    """按函数真实签名调用 arcpy 工具。

    GetParameterInfo 里的派生输出（如 CreateFolder 的 out_folder）不在 Python 函数
    签名中，直接位置展开会多传参数报 TypeError；按签名过滤掉它们。
    """
    positional = _positional_params(func)
    if positional is None:
        func(*values)
        return
    args = []
    for param, value in zip(kept, values):
        if param.name in positional:
            args.append(value)
    func(*args)


def gp_messages() -> str:
    try:
        return arcpy.GetMessages(0)
    except Exception:  # noqa: BLE001
        return ""


def _sr(sr) -> dict:
    if sr is None:
        return {"name": None, "wkid": None}
    return {
        "name": getattr(sr, "name", None),
        "wkid": getattr(sr, "factoryCode", None),
    }


def _extent(extent) -> dict | None:
    if extent is None:
        return None
    return {
        "xmin": getattr(extent, "XMin", None),
        "ymin": getattr(extent, "YMin", None),
        "xmax": getattr(extent, "XMax", None),
        "ymax": getattr(extent, "YMax", None),
    }


def _field_checks(output: str, field: str) -> dict:
    nulls = 0
    total = 0
    lo = None
    hi = None
    try:
        with arcpy.da.SearchCursor(output, [field]) as rows:
            for row in rows:
                total += 1
                value = row[0]
                if value is None:
                    nulls += 1
                    continue
                try:
                    if lo is None or value < lo:
                        lo = value
                    if hi is None or value > hi:
                        hi = value
                except TypeError:
                    pass
        return {"total": total, "null_count": nulls, "min": lo, "max": hi}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def validate_output(output: str, check_field: str | None) -> dict | None:
    """比例校验：输出存在、类型/CRS/extent/要素数，可选字段 null/极值。"""
    if not arcpy.Exists(output):
        return None
    desc = arcpy.Describe(output)
    record: dict = {
        "data_type": getattr(desc, "dataType", None),
        "shape_type": getattr(desc, "shapeType", None),
        "spatial_reference": _sr(getattr(desc, "spatialReference", None)),
        "extent": _extent(getattr(desc, "extent", None)),
    }
    try:
        record["count"] = int(arcpy.management.GetCount(output).getOutput(0))
    except Exception as exc:  # noqa: BLE001
        record["count"] = None
        record["count_error"] = str(exc)
    if check_field:
        record["field_checks"] = _field_checks(output, check_field)
    return record


def emit(record: dict) -> None:
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True, help="toolbox.tool 全名，如 analysis.Clip")
    parser.add_argument("--params", required=True, help='JSON 对象：官方参数名→值，如 {"in_features": "..."}')
    parser.add_argument("--output", required=True, help="主输出路径（必须不存在）")
    parser.add_argument("--output-param", help="主输出对应的参数名（默认自动识别）")
    parser.add_argument("--check-field", help="可选：对输出该字段做 null/极值校验")
    args = parser.parse_args()

    arcpy.env.overwriteOutput = False
    start = time.monotonic()
    try:
        toolbox, name = parse_tool(args.tool)
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise TypeError("--params 必须是 JSON 对象")
        output = os.path.abspath(args.output)
        info = arcpy.GetParameterInfo(args.tool)
        if not info:
            raise ValueError(
                f"无法解析工具 {args.tool}：请确认它属于白名单工具箱且本运行时已安装。"
                f"可用 arcpy.ListTools 列出。"
            )
        out_param = find_output_param(info, args.output_param)
        values, missing, kept = build_values(info, params, output, out_param.name)
        if missing:
            raise ValueError(
                f"缺少必填参数：{', '.join(missing)}。可用参数：{', '.join(p.name for p in kept if p.name)}"
            )
        bad_inputs = missing_inputs(kept, values)
        if bad_inputs:
            raise ValueError(f"输入数据集不存在：{', '.join(bad_inputs)}")
        if arcpy.Exists(output):
            raise ValueError(f"输出已存在，拒绝覆盖：{output}（请改用新版本化路径）")
        ensure_output_parent(output)

        func = resolve_tool(toolbox, name)
        call_tool(func, kept, values)

        validation = validate_output(output, args.check_field)
        record = {
            "ok": validation is not None,
            "tool": args.tool,
            "toolbox": toolbox,
            "output": output,
            "output_param": out_param.name,
            "duration_s": round(time.monotonic() - start, 3),
            "messages": gp_messages(),
            "validation": validation,
        }
        emit(record)
        return 0 if validation is not None else VALIDATION_FAILED_RC
    except Exception as exc:  # noqa: BLE001 - 兜底，转成 JSON 错误喂回调用方
        emit(
            {
                "ok": False,
                "tool": args.tool,
                "error": f"{type(exc).__name__}: {exc}",
                "duration_s": round(time.monotonic() - start, 3),
                "messages": gp_messages(),
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
