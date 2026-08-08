#!/usr/bin/env python
"""List installed official ArcPy tools for whitelisted toolboxes, with parameter names.

Read-only. 默认列出白名单工具箱（management / analysis / conversion）的已装工具，
每个工具带官方参数名 / 方向 / 是否必填；``--tool toolbox.tool`` 聚焦单个工具（含完整参数表）。

Emits a single JSON record last on stdout:
    {"ok": true, "toolboxes": {"management": [{"name": "AddField", "params": [...]}]},
     "total_tools": N}
Exit codes: ``0`` ok, ``1`` runtime error (JSON error record).
"""

from __future__ import annotations

import argparse
import json
import sys

import arcpy

ALLOWED_TOOLBOXES = ("management", "analysis", "conversion")
_PRECEDENCE = ("management", "analysis", "conversion")


def list_toolbox_tools(toolbox: str) -> list[str]:
    """列出某工具箱工具的完整名（如 ``Clip_analysis``）。

    ``arcpy.ListTools`` 只接受通配符；ArcPy 工具完整名遵循 ``名_工具箱`` 约定，
    按 ``*_<toolbox>`` 过滤即可精确命中该工具箱。逐级回退避免别名/名称差异。
    """
    pattern = f"*_{toolbox}"
    names = list(arcpy.ListTools(pattern) or [])
    if names:
        return sorted({str(n) for n in names})
    target = toolbox.lower()
    for tb in arcpy.ListToolboxes() or []:
        if f"({target})" in str(tb).lower():
            names = list(arcpy.ListTools(pattern) or [])
            if names:
                return sorted({str(n) for n in names})
    return []


def short_name(full: str) -> str:
    return full.rsplit("_", 1)[0] if "_" in full else full


def describe_tool(full: str) -> dict:
    info = arcpy.GetParameterInfo(full) or []
    params = []
    for p in info:
        name = getattr(p, "name", None)
        if not name:
            continue
        try:
            direction = p.direction
        except Exception:  # noqa: BLE001
            direction = None
        try:
            required = p.parameterType == "Required"
        except Exception:  # noqa: BLE001
            required = None
        try:
            datatype = p.datatype
        except Exception:  # noqa: BLE001
            datatype = None
        params.append({
            "name": name,
            "direction": direction,
            "required": required,
            "datatype": datatype,
        })
    return {"name": short_name(full), "params": params}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--toolboxes",
        default=",".join(ALLOWED_TOOLBOXES),
        help="逗号分隔工具箱别名，默认白名单全列",
    )
    parser.add_argument(
        "--tool", help="可选：只描述单个工具（toolbox.tool 全名），返回其完整参数表"
    )
    args = parser.parse_args()

    record: dict = {"ok": True}
    try:
        if args.tool:
            if "." not in args.tool:
                raise ValueError("--tool 需要 toolbox.tool 全名（如 management.Project）")
            toolbox, name = args.tool.split(".", 1)
            if toolbox not in ALLOWED_TOOLBOXES:
                raise ValueError(f"工具箱 {toolbox!r} 不在白名单：{'、'.join(ALLOWED_TOOLBOXES)}")
            record["focus"] = {"toolbox": toolbox, "tool": describe_tool(f"{name}_{toolbox}")}
        else:
            toolboxes = {}
            total = 0
            for raw in args.toolboxes.split(","):
                tb = raw.strip()
                if not tb:
                    continue
                tools = [describe_tool(t) for t in list_toolbox_tools(tb)]
                if not tools:
                    continue
                toolboxes[tb] = tools
                total += len(tools)
            record["toolboxes"] = toolboxes
            record["total_tools"] = total
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
