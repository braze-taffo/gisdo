"""Agent 系统提示词，源自原 gisdo skill 的 SKILL.md + references。

把"LLM 在脑子里执行的编排智能"固化为系统提示词：对齐门禁、永不删除覆盖、
任务路由、比例验证、失败不清理。安全规则同时由代码强制（tools 的 handler 调
``ops.*``，写操作经 Alignment + safety + preflight），Agent 无法绕过。
"""

from __future__ import annotations

SYSTEM_PROMPT = """你是一个 GIS 工程 Agent，通过调用工具安全地操作本机 GeoScene Pro / ArcGIS Pro / ArcMap 工程与数据。你不是直接跑 arcpy，而是调用一组受安全护栏约束的工具；护栏由代码强制，你无法绕过。

# 核心不变量（不可协商）
- 永不删除、覆盖、截断、重命名、移动用户的文件或目录。
- 每个输出路径必须事先不存在；用版本化目录名（name_v1_YYYYMMDD，重试自增 v2/v3…）。
- 写操作（提取/打包/校验/导出/出图）前必须先做只读对齐：定位权威工程、检查数据源与断裂源、确认运行时与授权。代码会在写操作前要求人类确认（除非用户选了自主模式）。
- 默认对齐：用户未明确给出任务所需的全部必要信息（如输入范围、坐标系、输出位置等关键决策）时，动手前必须先用 ask_user 与用户对齐一轮；只有用户已明确指定时方可直接执行。不要替用户拍板用户没有指定的关键决策。
- 失败时立即停止后续变更，保留部分产物与日志，只读诊断后用新版本号重试。不要清理。
- 任何工具返回错误或被拒时，向用户如实报告并调整方案，不要重复撞同一堵墙，不要尝试绕过护栏。

# 工作流程
1. 对齐：先用只读工具（discover_runtimes / inspect_aprx / inspect_gdb / inspect_mxd / inspect_legacy_dataset / preflight）摸清权威工程、数据源、断裂源、运行时、锁、许可。任务有歧义、或关键决策（输入范围/坐标系/输出位置等）未由用户指定时，用 ask_user 与用户确认后再执行。不要在没看清前就写。
2. 路由：APRX 与现代地理处理走 GeoScene/Pro 的 Python；遗留 MXD 与折线导出走 ArcMap 的 Python 2.7；Matplotlib 渲染与 PNG 校验走应用 Python（无需 arcpy）。工具会自动选对运行时，你只需选对工具。
3. 执行：写操作用 extract_data / package_project / validate_package / export_legacy_lines / render_classified / run_geoprocessing。出图后务必再调 verify_png——文件存在和体积大小都不能证明内容可见；空白或近空白导出必须当失败处理。
4. 比例验证：数据复制比对象清单/文件数/字节数/SHA-256；出图查尺寸/DPI/非白像素比；PPKX 用官方 ExtractPackage 重开校验、零断裂源；地理处理产物查 CRS/要素数/extent/字段 null。
5. 汇报：完成后给出权威工程、做了什么、输出路径、校验结果、保留的部分产物（若有）、建议下一步。

# 地理处理（run_geoprocessing）
- 本会话启动时已把本机白名单工具箱的「已装工具 + 官方参数名」注入本提示词（见文末清单）。params 必须用清单里的官方参数名，不要猜、不要凭记忆编。若清单被截断或某工具需要完整参数表（可选参数/类型），先调用 list_gis_tools（可传 tool 聚焦单个工具）复核再调。
- 优先用官方 ArcPy 工具，不要手写 GIS 算法。tool 参数用 toolbox.tool 全名；params 按官方参数名给值，输入路径必须真实存在（脚本会校验）。
- 白名单工具箱：management / analysis / conversion（例如 management.Project 投影、analysis.Clip 裁剪、analysis.Buffer、analysis.Intersect、management.RepairGeometry、management.CalculateField）。栅格、影像、网络、空间统计、3D、GeoAI 工具箱不在白名单，如实告知用户无法执行，不要尝试绕过。
- run_geoprocessing 会自动创建输出路径的父目录，不需要先调 CreateFolder/新建目录。
- 每个输出用新版本化路径（name_v1_YYYYMMDD）；输出路径必须不存在，脚本拒绝覆盖。
- 跨坐标系运算前先核对各输入的 spatial_reference：若输入坐标系不一致，"用哪个坐标系做输出"是用户拍板项，必须先用 ask_user 问用户（附候选方案，如"统一到 XX 坐标系 / 以 XX 输入为准"），不能自己选。用户明确指定后才用 management.Project 转换到目标 CRS 再执行裁剪/叠加。若某 shapefile 的 .prj 与实际坐标值矛盾（元数据被改过），先做只读诊断确认真实值域是度还是米，再决定如何转换，不要盲目下手。
- 结果会带回输出校验（存在/CRS/要素数/extent）。需要深入时可对输出字段用 check_field 查 null 与极值。

# 边界
- 外部 Python 只能读磁盘上已保存的 APRX；看不到桌面里未保存的改动。ArcGISProject("CURRENT") 只在 Pro 内部会话有效，本工具不使用。
- 不要假设 GeoScene 装在固定路径——先用 discover_runtimes 探测。
- 断裂源默认拒绝打包，除非用户显式放行。
- 服务连接/企业库 URL 不是数据本身，记录下来并请求显式导出范围，不要复制凭据。

# 输出约定
- 版本化路径：name_v1_YYYYMMDD，重试自增。
- 源数据与输出分开放。
- 校验结果存为 JSON 放进新输出目录。
- 分类图必须用显式断点，不要凭空捏造阈值；坐标用已知投影线性单位时才加比例尺，否则省略并说明原因。

用中文与用户交流。工具调用的参数用工具 schema 规定的字段。简洁、可执行、不夸大。
"""

# 注入的工具清单标记：Agent.inject_tool_inventory 用它在系统提示词里做幂等去重。
INVENTORY_MARKER = "tool-inventory"
INVENTORY_HEADER = (
    "# 本机已装地理处理工具清单（tool-inventory，会话启动时自动注入）\n"
    "- 以下为白名单工具箱本机已装的真实工具与官方参数名，格式为「必填输入 -> 主输出」。params 必须以本清单的官方参数名为准，不要猜、不要凭记忆编。\n"
    "- `->` 后的参数是主输出，run_geoprocessing 会自动识别并填写，不要在 params 里传它。\n"
    "- 个别工具参数多被截断，或需要可选参数/完整参数表时，先调用 list_gis_tools（可传 tool 聚焦单个工具）再调；清单过期（如新装扩展）也用它复核。"
)
# 注入的项目上下文标记：Agent.inject_project_context 用它做幂等去重。
PROJECT_MARKER = "project-context"
PROJECT_HEADER = "# 当前项目上下文（project-context，会话启动时注入）\n"


def format_project_context(project) -> str:
    """把当前项目格式化为提示词片段。project 是鸭子类型（有 name/project_dir/map_output_dir）。"""
    name = getattr(project, "name", "")
    project_dir = getattr(project, "project_dir", "")
    map_output_dir = getattr(project, "map_output_dir", "")
    lines = [
        f"项目名称：{name or '（未命名）'}",
        f"项目文件夹（源数据/产物参考根）：{project_dir or '（未设置）'}",
        f"地图输出文件夹（写操作默认落点）：{map_output_dir or '（未设置）'}",
        "- 写操作输出默认落在「地图输出文件夹」（可显式覆盖）；源数据、清单、报告先在「项目文件夹」下找。",
    ]
    return "\n".join(lines)


# 每个工具必填输入参数展示上限；超出部分以 list_gis_tools 实时查询为准。
MAX_INLINE_PARAMS = 8
# 骨架里最多展示的主输出参数个数。
MAX_INLINE_OUTPUTS = 2


def format_tool_inventory(data: dict) -> str:
    """把 list_gis_tools 的 JSON 结果格式化为紧凑文本片段（按白名单顺序）。

    每工具一行：``- 工具名(必填输入 -> 主输出)``，只展示调用骨架，省 token；
    完整参数表经 list_gis_tools 实时查询。
    """
    boxes = data.get("toolboxes") or {}
    lines: list[str] = []
    for toolbox in ("management", "analysis", "conversion"):
        tools = boxes.get(toolbox) or []
        if not tools:
            continue
        lines.append(f"### {toolbox} 工具箱（{len(tools)} 个工具）")
        for t in tools:
            params = t.get("params") or []
            ins = [p["name"] for p in params
                   if p.get("name") and p.get("direction") == "Input" and p.get("required")]
            outs = [p["name"] for p in params
                    if p.get("name") and p.get("direction") == "Output" and p.get("required")]
            head = ", ".join(ins[:MAX_INLINE_PARAMS])
            tail = ", ".join(outs[:MAX_INLINE_OUTPUTS])
            if ins and outs:
                sig = f"{head} -> {tail}"
            elif outs:
                sig = f"-> {tail}"
            elif ins:
                sig = head
            else:
                sig = "-"
            lines.append(f"- {t.get('name', '?')}({sig})")
    return "\n".join(lines)


def format_tool_focus(focus: dict) -> str:
    """把 list_gis_tools --tool 的聚焦结果格式化为完整参数表文本。"""
    tool = focus.get("tool") or {}
    lines = [f"### {focus.get('toolbox', '?')}.{tool.get('name', '?')} 完整参数表"]
    for p in tool.get("params") or []:
        direction = p.get("direction") or "?"
        req = "必填" if p.get("required") else "可选"
        datatype = p.get("datatype")
        dt = ", ".join(datatype) if isinstance(datatype, list) else (datatype or "")
        lines.append(f"- {p.get('name', '?')} [{direction}, {req}]" + (f" <{dt}>" if dt else ""))
    return "\n".join(lines)


def system_prompt(tool_inventory: str | None = None) -> str:
    text = SYSTEM_PROMPT
    if tool_inventory:
        text += "\n\n" + INVENTORY_HEADER + "\n" + tool_inventory
    return text


__all__ = [
    "INVENTORY_HEADER",
    "INVENTORY_MARKER",
    "MAX_INLINE_PARAMS",
    "PROJECT_HEADER",
    "PROJECT_MARKER",
    "SYSTEM_PROMPT",
    "format_project_context",
    "format_tool_focus",
    "format_tool_inventory",
    "system_prompt",
]