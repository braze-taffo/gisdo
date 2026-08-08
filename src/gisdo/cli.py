"""命令行入口。

只读命令直接输出 JSON；写命令先打印对齐确认块，需 ``--yes`` 才执行
（对应 SKILL.md 的"报告并等待确认"）。运行时可用 ``--python``/``--arcmap-python``
覆盖，否则自动发现。

子命令：discover / inspect / extract / package / validate / render / verify / preflight / project / chat / gui
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from typing import Any

from gisdo.agent import (
    AUTONOMY_AUTONOMOUS,
    AUTONOMY_CONFIRM_EVERY_STEP,
    AUTONOMY_CONFIRM_WRITES,
    Agent,
    AgentCallbacks,
    LlmClient,
    LlmConfig,
    ToolContext,
    build_tool_inventory,
)
from gisdo.agent.prompt import format_project_context
from gisdo.config import Settings
from gisdo.engine import failure as failure_mod
from gisdo.engine import ops
from gisdo.engine import preflight as preflight_mod
from gisdo.engine import runtime as runtime_mod
from gisdo.engine.alignment import Alignment, build_draft
from gisdo.engine.runner import RunCancelled, ScriptResult
from gisdo.engine.runtime import Runtime
from gisdo.engine.safety import SafetyError
from gisdo.engine.versioning import versioned_path
from gisdo.project import GisProject, ProjectStore, history_path

# --------------------------------------------------------------------------- #
# 运行时解析
# --------------------------------------------------------------------------- #


def _resolve_modern(args: argparse.Namespace) -> Runtime:
    if getattr(args, "python", None):
        return Runtime(python=args.python, family="explicit", source="explicit")
    runtime, _ = runtime_mod.discover_first()
    if runtime is None:
        _die("未发现 GeoScene Pro / ArcGIS Pro 运行时。请用 --python 指定，或设置 GEOSCENE_PYTHON 环境变量。")
    return runtime


def _resolve_arcmap(args: argparse.Namespace) -> Runtime:
    if getattr(args, "arcmap_python", None):
        return Runtime(python=args.arcmap_python, family="ArcMap", is_py2=True, source="explicit")
    discovery = runtime_mod.list_runtimes()
    if not discovery.legacy_arcmap_candidates:
        _die("未发现 ArcMap Python 2.7 运行时。请用 --arcmap-python 指定，或设置 ARCMAP_PYTHON 环境变量。")
    return Runtime(
        python=discovery.legacy_arcmap_candidates[0],
        family="ArcMap",
        is_py2=True,
        source="discover",
    )


def _try_modern(args: argparse.Namespace) -> Runtime | None:
    """软解析：找不到返回 None（chat 模式允许无运行时启动）。"""
    if getattr(args, "python", None):
        return Runtime(python=args.python, family="explicit", source="explicit")
    runtime, _ = runtime_mod.discover_first()
    return runtime


def _try_arcmap(args: argparse.Namespace) -> Runtime | None:
    if getattr(args, "arcmap_python", None):
        return Runtime(python=args.arcmap_python, family="ArcMap", is_py2=True, source="explicit")
    discovery = runtime_mod.list_runtimes()
    if not discovery.legacy_arcmap_candidates:
        return None
    return Runtime(python=discovery.legacy_arcmap_candidates[0],
                   family="ArcMap", is_py2=True, source="discover")


# --------------------------------------------------------------------------- #
# 输出辅助
# --------------------------------------------------------------------------- #


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _stream(prefix: str):
    def cb(line: str) -> None:
        print(f"[{prefix}] {line}", file=sys.stderr)
    return cb


def _die(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _report_result(result: ScriptResult, *, json_output: bool = True) -> int:
    if json_output and result.json is not None:
        _print_json(result.json)
    elif result.stdout.strip():
        print(result.stdout)
    if result.failed:
        if result.stderr.strip():
            print(result.stderr, file=sys.stderr)
        return 1
    if result.validation_failed:
        print("校验失败（退出码 3）。详见上方报告。", file=sys.stderr)
        return 3
    return 0


def _finish_write(result: ScriptResult, output_path: str) -> int:
    """写命令收尾：成功打印报告；失败打印结构化失败报告（含部分产物与重试路径）。"""
    if result.ok:
        return _report_result(result)
    rec = failure_mod.FailureRecord.from_result(result, output_path=output_path)
    print(rec.format_report(), file=sys.stderr)
    return 3 if result.validation_failed else 1


def _make_cancel() -> threading.Event:
    return threading.Event()


# --------------------------------------------------------------------------- #
# 对齐门禁（CLI 版）
# --------------------------------------------------------------------------- #


def _gate_write(args: argparse.Namespace, *, project: str, output: str,
                inventory: dict | None = None, will_create: list[str] | None = None,
                classification: str = "", output_format: str = "") -> Alignment:
    alignment = build_draft(
        project=project,
        inventory=inventory,
        output_location=output,
        will_create=will_create,
        classification_field=classification,
        output_format=output_format,
        desktop_authorized=getattr(args, "desktop", False),
    )
    print("===== 对齐确认块 =====", file=sys.stderr)
    print(alignment.as_block(), file=sys.stderr)
    print("======================", file=sys.stderr)
    if not getattr(args, "yes", False):
        _die("已打印对齐块。确认无误后请加 --yes 重新执行。", code=0)
    alignment.confirm()
    return alignment


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #


def cmd_discover(args: argparse.Namespace) -> int:
    if args.probe:
        runtime = _resolve_modern(args)
        probe = runtime_mod.probe(runtime.python)
        _print_json(probe)
        return 0
    discovery = runtime_mod.list_runtimes()
    _print_json({
        "modern_candidates": discovery.modern_candidates,
        "legacy_arcmap_candidates": discovery.legacy_arcmap_candidates,
    })
    return 0 if discovery.has_any else 2


def cmd_inspect(args: argparse.Namespace) -> int:
    kind = args.kind
    if kind == "aprx":
        rt = _resolve_modern(args)
        result = ops.inspect_aprx(rt, args.path, on_output=_stream("aprx"))
    elif kind == "gdb":
        rt = _resolve_modern(args)
        result = ops.inspect_gdb(rt, args.path, skip_counts=args.skip_counts, on_output=_stream("gdb"))
    elif kind == "mxd":
        rt = _resolve_arcmap(args)
        result = ops.inspect_mxd(rt, args.path, on_output=_stream("mxd"))
    elif kind == "dataset":
        rt = _resolve_arcmap(args)
        result = ops.inspect_legacy_dataset(rt, args.path, on_output=_stream("dataset"))
    else:
        _die(f"未知检查类型：{kind}")
    return _report_result(result)


def cmd_extract(args: argparse.Namespace) -> int:
    rt = _resolve_modern(args)
    inventory = None
    if not args.skip_inventory:
        inv_result = ops.inspect_aprx(rt, args.project)
        if inv_result.json:
            inventory = inv_result.json
    out = args.output_dir or str(versioned_path(Path("."), "extract"))
    alignment = _gate_write(
        args, project=args.project, output=out, inventory=inventory,
        will_create=["extraction_manifest.json", "workspaces/", "files/"],
    )
    result = ops.extract_data(
        rt, args.project, out, alignment=alignment, skip_hashes=args.skip_hashes,
        on_output=_stream("extract"),
    )
    return _finish_write(result, out)


def cmd_package(args: argparse.Namespace) -> int:
    rt = _resolve_modern(args)
    inventory = None
    if not args.skip_inventory:
        inv_result = ops.inspect_aprx(rt, args.project)
        if inv_result.json:
            inventory = inv_result.json
    out = args.output_ppkx or str(versioned_path(Path("."), "package", version=1).with_suffix(".ppkx"))
    alignment = _gate_write(
        args, project=args.project, output=out, inventory=inventory,
        will_create=[Path(out).name],
    )
    result = ops.package_project(
        rt, args.project, out, alignment=alignment, inventory=inventory,
        allow_broken=args.allow_broken, summary=args.summary, tags=args.tags,
        on_output=_stream("package"),
    )
    return _finish_write(result, out)


def cmd_validate(args: argparse.Namespace) -> int:
    rt = _resolve_modern(args)
    out = args.output_dir or str(versioned_path(Path("."), "validate"))
    alignment = _gate_write(
        args, project=args.package, output=out,
        will_create=["validation_report.json", "提取出的 APRX"],
    )
    result = ops.validate_package(
        rt, args.package, out, alignment=alignment, source_aprx=args.source_aprx,
        on_output=_stream("validate"),
    )
    return _finish_write(result, out)


def cmd_render(args: argparse.Namespace) -> int:
    breaks = [float(x) for x in args.breaks.split(",") if x.strip()]
    options = ops.RenderOptions(
        breaks=breaks,
        colors=args.colors.split(",") if args.colors else None,
        labels=args.labels.split("|") if args.labels else None,
        title=args.title,
        legend_title=args.legend_title,
        scale_bar=args.scale_bar,
        scale_label=args.scale_label,
        output_pdf=args.output_pdf,
        report=args.report,
        axis_km=args.axis_km,
        no_north_arrow=args.no_north_arrow,
        no_grid=args.no_grid,
        line_width=args.line_width,
        width=args.width,
        height=args.height,
        dpi=args.dpi,
    )
    alignment = _gate_write(
        args, project=args.input_json, output=args.output_png,
        will_create=[args.output_png] + ([args.output_pdf] if args.output_pdf else []),
        classification=f"breaks={breaks}",
        output_format=f"PNG {args.width}x{args.height}in @ {args.dpi}dpi",
    )
    result = ops.render_classified(
        args.input_json, args.output_png, options, alignment=alignment,
        on_output=_stream("render"),
    )
    return _finish_write(result, args.output_png)


def cmd_verify(args: argparse.Namespace) -> int:
    result = ops.verify_png(args.png, on_output=_stream("verify"))
    return _report_result(result)


def cmd_preflight(args: argparse.Namespace) -> int:
    rt = _resolve_modern(args)
    inventory = None
    gdb_roots: list[str] = []
    if args.project:
        inv_result = ops.inspect_aprx(rt, args.project)
        if inv_result.json:
            inventory = inv_result.json
            for src in inventory.get("data_sources", []) or []:
                low = str(src).lower().replace("\\", "/")
                idx = low.find(".gdb")
                if idx >= 0:
                    root = str(src)[: idx + 4]
                    if root not in gdb_roots:
                        gdb_roots.append(root)
    out = args.output or str(versioned_path(Path("."), args.base or "output"))
    report = preflight_mod.preflight(
        runtime=rt, output_path=out, inventory=inventory,
        gdb_roots=gdb_roots, allow_broken=args.allow_broken,
    )
    print(report.format())
    return 0 if report.ok else 1


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from gisdo.gui.app import main as gui_main
    except ImportError as exc:
        _die(f"GUI 依赖缺失（PySide6）：{exc}\n请运行 pip install gisdo[gui] 或 pip install PySide6。")
    return gui_main()


# --------------------------------------------------------------------------- #
# Agent 对话（gisdo chat）
# --------------------------------------------------------------------------- #

_AUTONOMY_CHOICES = {
    "confirm-writes": AUTONOMY_CONFIRM_WRITES,
    "autonomous": AUTONOMY_AUTONOMOUS,
    "confirm-every-step": AUTONOMY_CONFIRM_EVERY_STEP,
}


def _chat_confirm(name: str, args: dict, alignment) -> bool:
    print(f"\n📋 请求确认写操作：{name}")
    print(f"   参数：{json.dumps(args, ensure_ascii=False)}")
    if alignment is not None:
        print("   对齐确认块：")
        for line in alignment.as_block().splitlines():
            print(f"     {line}")
    try:
        ans = input("   批准？(y/n) > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes", "是")


def _chat_ask(question: str, options: list[str]) -> str | None:
    print(f"\n❓ {question}")
    if options:
        for idx, opt in enumerate(options, 1):
            print(f"   {idx}. {opt}")
    try:
        if options:
            ans = input(f"   选择 (1-{len(options)}) 或直接输入 > ").strip()
        else:
            ans = input("   > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not ans:
        return None
    if options and ans.isdigit() and 1 <= int(ans) <= len(options):
        return options[int(ans) - 1]
    return ans


# --------------------------------------------------------------------------- #
# 项目管理（gisdo project）
# --------------------------------------------------------------------------- #


def _find_project(store: ProjectStore, name: str) -> GisProject:
    project = store.get(name) or store.get_by_name(name)
    if project is None:
        _die(f"未找到项目：{name}。用 gisdo project list 查看。")
    return project


def cmd_project_list(_args: argparse.Namespace) -> int:
    store = ProjectStore.load()
    if not store.projects:
        print("（暂无项目）")
        return 0
    current = store.current_project_id
    for p in store.projects:
        mark = "*" if p.id == current else " "
        print(f"{mark} {p.name}  [{p.id}]")
        print(f"   项目文件夹：{p.project_dir or '（未设置）'}")
        print(f"   地图输出：{p.map_output_dir or '（未设置）'}")
    return 0


def cmd_project_new(args: argparse.Namespace) -> int:
    store = ProjectStore.load()
    if store.get_by_name(args.name):
        _die(f"已存在同名项目：{args.name}")
    project = store.create(args.name, args.project_dir or "", args.map_output_dir or "")
    store.set_current(project.id)
    print(f"已创建并设为当前项目：{project.name} [{project.id}]")
    print(f"   地图输出：{project.map_output_dir or '（未设置）'}")
    return 0


def cmd_project_use(args: argparse.Namespace) -> int:
    store = ProjectStore.load()
    project = _find_project(store, args.name)
    store.set_current(project.id)
    print(f"已设为当前项目：{project.name}")
    return 0


def cmd_project_rm(args: argparse.Namespace) -> int:
    import shutil

    store = ProjectStore.load()
    project = _find_project(store, args.name)
    store.delete(project.id)
    shutil.rmtree(history_path(project.id).parent, ignore_errors=True)
    print(f"已删除项目：{project.name}（含对话历史）")
    return 0


def _resolve_project(args: argparse.Namespace) -> GisProject | None:
    """解析 chat 的项目：--new-project 优先，其次 --project，最后当前激活项目。"""
    store = ProjectStore.load()
    if getattr(args, "new_project", None):
        if store.get_by_name(args.new_project):
            _die(f"已存在同名项目：{args.new_project}")
        return store.create(args.new_project, args.project_dir or "", args.map_output_dir or "")
    if getattr(args, "project", None):
        return _find_project(store, args.project)
    return store.current()


def _save_history_file(path: str, messages: list[dict]) -> None:
    """落盘对话历史；失败静默（不阻断对话）。"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": 1, "messages": messages}, ensure_ascii=False),
                     encoding="utf-8")
    except OSError:
        pass


def cmd_chat(args: argparse.Namespace) -> int:
    try:
        import openai  # noqa: F401
    except ImportError:
        _die("未安装 openai SDK。请运行 pip install gisdo[ai] 或 pip install openai。")

    settings = Settings.load()
    base_url = args.base_url or settings.ai_base_url
    api_key = args.api_key or settings.ai_api_key
    model = args.model or settings.ai_model
    autonomy = _AUTONOMY_CHOICES.get(args.autonomy, settings.autonomy_mode or AUTONOMY_CONFIRM_WRITES)
    if not base_url or not model:
        _die("未配置 LLM。请用 --base-url/--model 指定，或在 GUI 设置里填写后重试。")

    config = LlmConfig(base_url=base_url, api_key=api_key, model=model)
    client = LlmClient(config)

    import threading
    cancel = threading.Event()
    modern = _try_modern(args)
    arcmap = _try_arcmap(args)
    proj = _resolve_project(args)
    history_file = str(history_path(proj.id)) if proj is not None else None
    ctx = ToolContext(modern_runtime=modern, arcmap_runtime=arcmap, cancel=cancel,
                      on_log=lambda line: print(f"   │ {line}"),
                      project=proj)

    if args.verbose:

        def _tool_start(n, a):
            print(f"\n🔧 {n}({json.dumps(a, ensure_ascii=False)})")

        def _tool_end(name, result):
            preview = result if len(result) <= 300 else result[:300] + "…"
            print(f"   ↳ {name}：{preview}")
    else:

        def _tool_start(n, _a):
            print(f"\n🔧 {n}（参数与原始输出已隐藏，加 --verbose 查看）")

        def _tool_end(_name, _result):
            return

    _stream_open = False

    def _on_token(t):
        nonlocal _stream_open
        if not _stream_open:
            print("\n🤖 ", end="", flush=True)
            _stream_open = True
        print(t, end="", flush=True)

    def _on_text(t):
        nonlocal _stream_open
        if _stream_open:
            print()  # 流式后的收尾换行
            _stream_open = False
        else:
            print(f"\n🤖 {t}")  # 非流式（无 content）时保持原样

    callbacks = AgentCallbacks(
        on_assistant_text=_on_text,
        on_token=_on_token,
        on_tool_start=_tool_start,
        on_tool_end=_tool_end,
        on_confirm=_chat_confirm,
        on_ask_user=_chat_ask,
        on_error=lambda m: print(f"\n⚠️ {m}", file=sys.stderr),
        on_info=lambda m: print(f"ℹ️ {m}"),
    )
    agent = Agent(client.chat, ctx, callbacks=callbacks, autonomy=autonomy, cancel=cancel)
    if proj is not None:
        agent.inject_project_context(format_project_context(proj))
        if history_file and Path(history_file).is_file():
            try:
                data = json.loads(Path(history_file).read_text(encoding="utf-8"))
                agent.load_history(data.get("messages", []) or [])
                if data.get("messages"):
                    print(f"   · 已恢复项目「{proj.name}」的对话历史（{len(data['messages'])} 条消息）。")
            except (OSError, ValueError):
                pass

    rt_info = f"现代运行时={'有' if modern else '无'}，ArcMap={'有' if arcmap else '无'}"
    proj_info = f"项目={proj.name if proj else '无'}"
    print(f"GISdo Agent 就绪。模型={model}，自主={args.autonomy}，{rt_info}，{proj_info}。")
    if modern:
        inventory = build_tool_inventory(modern)
        if inventory:
            agent.inject_tool_inventory(inventory)
            print("   · 已注入本机已装地理处理工具清单（模型直接用真实官方参数名）。")
        else:
            print("   · 工具清单注入失败，模型将用 list_gis_tools 工具实时查询。")
    print("命令：/quit 退出 · /reset 清空对话 · Ctrl+C 中断当前任务。")
    while True:
        try:
            user = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        if not user:
            continue
        if user in ("/quit", "/exit"):
            return 0
        if user == "/reset":
            agent.reset()
            if history_file:
                _save_history_file(history_file, [])
            print("已清空对话历史。")
            continue
        try:
            agent.run(user)
            if history_file:
                _save_history_file(history_file, [
                    m for m in agent.history if m.get("role") != "system"
                ])
        except RunCancelled:  # 覆盖 LlmCancelled
            print("\n⚠️ 已取消。")
        except KeyboardInterrupt:
            cancel.set()
            print("\n⚠️ 已中断当前任务（子进程将终止）。")
            cancel.clear()
        except Exception as exc:  # noqa: BLE001
            print(f"\n⚠️ {type(exc).__name__}: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# 参数解析
# --------------------------------------------------------------------------- #


def _add_runtime_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--python", help="GeoScene/ArcGIS Pro Python 解释器路径（覆盖自动发现）")
    p.add_argument("--arcmap-python", help="ArcMap Python 2.7 解释器路径")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gisdo", description="GeoScene / ArcGIS 安全工作台")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="发现/探测本机运行时")
    p.add_argument("--probe", action="store_true", help="探测第一个 Pro 运行时的 arcpy 详情")
    _add_runtime_args(p)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("inspect", help="只读检查工程/数据")
    p.add_argument("kind", choices=["aprx", "gdb", "mxd", "dataset"])
    p.add_argument("path", help=".aprx / .gdb / .mxd / 数据集路径")
    p.add_argument("--skip-counts", action="store_true", help="gdb: 跳过 GetCount")
    _add_runtime_args(p)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("extract", help="提取 APRX 引用的本地数据到新版本化目录")
    p.add_argument("project", help=".aprx 路径")
    p.add_argument("output_dir", nargs="?", help="输出目录（默认 ./extract_v1_日期）")
    p.add_argument("--skip-hashes", action="store_true")
    p.add_argument("--skip-inventory", action="store_true", help="跳过提取前的只读检查")
    p.add_argument("--yes", action="store_true", help="确认对齐块并执行")
    p.add_argument("--desktop", action="store_true")
    _add_runtime_args(p)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("package", help="用官方 PackageProject 打包为 PPKX")
    p.add_argument("project", help=".aprx 路径")
    p.add_argument("output_ppkx", nargs="?")
    p.add_argument("--allow-broken", action="store_true")
    p.add_argument("--summary", default="Portable GeoScene project package")
    p.add_argument("--tags", default="GeoScene;GIS;ArcPy")
    p.add_argument("--skip-inventory", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--desktop", action="store_true")
    _add_runtime_args(p)
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("validate", help="用官方 ExtractPackage 校验 PPKX")
    p.add_argument("package", help=".ppkx 路径")
    p.add_argument("output_dir", nargs="?")
    p.add_argument("--source-aprx", help="源 APRX，用于布局签名比对")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--desktop", action="store_true")
    _add_runtime_args(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("render", help="把分类线 JSON 渲染为 PNG/PDF")
    p.add_argument("input_json", help="export_legacy_lines 产出的 JSON")
    p.add_argument("output_png", help="新 PNG 路径（必须不存在）")
    p.add_argument("--breaks", required=True, help="逗号分隔的分类断点")
    p.add_argument("--colors", help="逗号分隔的颜色")
    p.add_argument("--labels", help="竖线分隔的图例标签")
    p.add_argument("--title", default="")
    p.add_argument("--legend-title", default="")
    p.add_argument("--scale-bar", type=float)
    p.add_argument("--scale-label")
    p.add_argument("--output-pdf")
    p.add_argument("--report")
    p.add_argument("--axis-km", action="store_true")
    p.add_argument("--no-north-arrow", action="store_true")
    p.add_argument("--no-grid", action="store_true")
    p.add_argument("--line-width", type=float, default=1.1)
    p.add_argument("--width", type=float, default=10.0)
    p.add_argument("--height", type=float, default=6.5)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--desktop", action="store_true")
    _add_runtime_args(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("verify", help="像素校验 PNG（拒绝空白导出）")
    p.add_argument("png", help="PNG 路径")
    _add_runtime_args(p)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("preflight", help="写前预检（运行时/输出路径/锁/断裂源）")
    p.add_argument("--project", help="APRX 路径，用于读取数据源与断裂源")
    p.add_argument("--output", help="拟输出路径（默认自动版本化 ./output_v1_日期）")
    p.add_argument("--base", default="output", help="自动版本化输出名前缀")
    p.add_argument("--allow-broken", action="store_true", help="放行断裂源")
    _add_runtime_args(p)
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("project", help="项目管理：list / new / use / rm")
    psub = p.add_subparsers(dest="project_cmd", required=True)
    psub.add_parser("list", help="列出所有项目").set_defaults(func=cmd_project_list)
    pnew = psub.add_parser("new", help="新建项目")
    pnew.add_argument("name", help="项目名称")
    pnew.add_argument("--project-dir", help="项目文件夹")
    pnew.add_argument("--map-output-dir", help="地图输出文件夹")
    pnew.set_defaults(func=cmd_project_new)
    puse = psub.add_parser("use", help="设为当前项目")
    puse.add_argument("name", help="项目 id 或名称")
    puse.set_defaults(func=cmd_project_use)
    prm = psub.add_parser("rm", help="删除项目（含对话历史）")
    prm.add_argument("name", help="项目 id 或名称")
    prm.set_defaults(func=cmd_project_rm)
    p.set_defaults(func=cmd_project_list)

    p = sub.add_parser("gui", help="启动图形界面")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("chat", help="AI Agent 对话：用自然语言下 GIS 任务")
    p.add_argument("--base-url", help="OpenAI 兼容端点（默认读 settings.json）")
    p.add_argument("--api-key", help="API key（默认读 settings.json 或 GISDO_API_KEY）")
    p.add_argument("--model", help="模型名（默认读 settings.json）")
    p.add_argument("--autonomy", choices=list(_AUTONOMY_CHOICES), default="confirm-writes",
                   help="自主程度：confirm-writes(默认)/autonomous/confirm-every-step")
    p.add_argument("--verbose", action="store_true",
                   help="显示工具调用完整参数与原始输出（默认隐藏）")
    p.add_argument("--project", help="按 id 或名称选择项目（默认用当前激活项目）")
    p.add_argument("--new-project", help="创建新项目并进入对话")
    p.add_argument("--project-dir", help="配合 --new-project：项目文件夹")
    p.add_argument("--map-output-dir", help="配合 --new-project：地图输出文件夹")
    _add_runtime_args(p)
    p.set_defaults(func=cmd_chat)

    return parser


def main(argv: list[str] | None = None) -> int:
    from gisdo.config import seed_defaults_if_missing

    seed_defaults_if_missing()  # 打包后首次运行预置模型配置（如无 settings.json）
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RunCancelled:
        print("已取消。", file=sys.stderr)
        return 130
    except SafetyError as exc:
        print(f"安全校验未通过：{exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
