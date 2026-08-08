"""list_gis_tools 工具 + 上下文注入测试（monkeypatch ops，避免真实 subprocess / arcpy）。"""

import types

from gisdo.agent import prompt as prompt_mod
from gisdo.agent import tools as tools_mod
from gisdo.agent.llm import AssistantMessage
from gisdo.agent.loop import Agent
from gisdo.agent.prompt import (
    INVENTORY_MARKER,
    MAX_INLINE_PARAMS,
    format_tool_inventory,
    system_prompt,
)
from gisdo.agent.tools import ToolContext, default_registry
from gisdo.engine.runner import ScriptResult


def _result(returncode=0, json_data=None):
    return ScriptResult(script="list_gis_tools.py", interpreter="python", args=[],
                        returncode=returncode, stdout="", stderr="", duration_s=0.1,
                        json=json_data, json_error=None)


def _rt():
    return types.SimpleNamespace(python="C:/fake/python.exe", is_py2=False)


def _ctx():
    ctx = ToolContext()
    ctx.modern_runtime = _rt()
    return ctx


def _call(name, args, ctx):
    return default_registry().get(name).call(args, ctx)


SAMPLE = {
    "ok": True,
    "total_tools": 3,
    "toolboxes": {
        "conversion": [{"name": "FeatureClassToFeatureClass", "params": [
            {"name": "in_features", "direction": "Input", "required": True},
            {"name": "out_path", "direction": "Input", "required": True},
            {"name": "out_name", "direction": "Input", "required": True}]}],
        "analysis": [{"name": "Clip", "params": [
            {"name": "in_features", "direction": "Input", "required": True},
            {"name": "clip_features", "direction": "Input", "required": True},
            {"name": "out_feature_class", "direction": "Output", "required": True}]}],
        "management": [{"name": "Project", "params": [
            {"name": f"p{i}", "direction": "Input", "required": True} for i in range(20)]}],
    },
}


# --------------------------------------------------------------------------- #
# ops 层
# --------------------------------------------------------------------------- #


def test_ops_list_gis_tools_dispatch(monkeypatch):
    captured = {}

    def fake_run_script(interpreter, script, args, **kwargs):
        captured["interpreter"] = interpreter
        captured["script"] = script
        captured["args"] = args
        return _result(json_data={"ok": True})

    monkeypatch.setattr(tools_mod.ops, "run_script", fake_run_script)
    from gisdo.engine import ops as ops_mod
    ops_mod.list_gis_tools(_rt())
    assert captured["script"] == "list_gis_tools.py"
    assert captured["interpreter"] == "C:/fake/python.exe"
    assert captured["args"] == ["--toolboxes", "management,analysis,conversion"]


def test_ops_list_gis_tools_focus(monkeypatch):
    captured = {}
    monkeypatch.setattr(tools_mod.ops, "run_script",
                        lambda *a, **k: captured.update(args=a[2]) or _result())
    from gisdo.engine import ops as ops_mod
    ops_mod.list_gis_tools(_rt(), tool="management.Project")
    assert "--tool" in captured["args"]
    assert "management.Project" in captured["args"]


# --------------------------------------------------------------------------- #
# tools 层
# --------------------------------------------------------------------------- #


def test_list_gis_tools_requires_runtime():
    out = _call("list_gis_tools", {}, ToolContext())
    assert "未选定" in out


def test_list_gis_tools_default(monkeypatch, tmp_path):
    _no_cache(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: captured.update(rt=rt, kw=k) or _result(json_data=SAMPLE))
    ctx = _ctx()
    out = _call("list_gis_tools", {}, ctx)
    assert captured["rt"] is ctx.modern_runtime
    assert "Clip" in out
    assert "Project" in out


def test_list_gis_tools_toolboxes(monkeypatch, tmp_path):
    _no_cache(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: captured.update(kw=k) or _result(json_data=SAMPLE))
    _call("list_gis_tools", {"toolboxes": ["management"]}, _ctx())
    assert captured["kw"]["toolboxes"] == ("management",)


def test_list_gis_tools_focus(monkeypatch, tmp_path):
    captured = {}
    focus_data = {"ok": True, "focus": {"toolbox": "management", "tool": {
        "name": "Project", "params": [
            {"name": "in_dataset", "direction": "Input", "required": True, "datatype": "要素图层"},
            {"name": "out_dataset", "direction": "Output", "required": True, "datatype": None},
        ]}}}
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: captured.update(kw=k) or _result(json_data=focus_data))
    out = _call("list_gis_tools", {"tool": "management.Project"}, _ctx())
    assert captured["kw"]["tool"] == "management.Project"
    assert "完整参数表" in out
    assert "in_dataset [Input, 必填] <要素图层>" in out


def test_list_gis_tools_serves_cache_without_subprocess(monkeypatch, tmp_path):
    _no_cache(tmp_path, monkeypatch)
    tools_mod.ops.write_gis_tools_cache(_rt().python, SAMPLE)
    called = []
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: called.append(1) or _result(json_data=SAMPLE))
    out = _call("list_gis_tools", {}, _ctx())
    assert called == []
    assert "缓存快照" in out
    assert "Clip(" in out


def test_list_gis_tools_live_scan_writes_cache(monkeypatch, tmp_path):
    _no_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: _result(json_data=SAMPLE))
    _call("list_gis_tools", {}, _ctx())
    data = tools_mod.ops.read_gis_tools_cache(_rt().python)
    assert data is not None and data["total_tools"] == 3


def test_list_gis_tools_read_only_in_registry():
    tool = default_registry().get("list_gis_tools")
    assert tool is not None
    assert tool.is_write is False
    schema = tool.schema()["function"]
    assert schema["parameters"]["required"] == []


# --------------------------------------------------------------------------- #
# build_tool_inventory
# --------------------------------------------------------------------------- #


def test_build_inventory_none_without_runtime():
    assert tools_mod.build_tool_inventory(None) is None


def _no_cache(tmp_path, monkeypatch):
    """让缓存读写指向临时目录，避免测试污染真实 LOCALAPPDATA 缓存。"""
    monkeypatch.setattr(tools_mod.ops, "gis_tools_cache_path", lambda: tmp_path / "cache.json")


def test_build_inventory_none_on_bad_result(monkeypatch, tmp_path):
    _no_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: _result(json_data={"ok": False, "error": "x"}))
    assert tools_mod.build_tool_inventory(_rt()) is None


def test_build_inventory_none_on_exception(monkeypatch, tmp_path):
    _no_cache(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("no arcpy")
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools", boom)
    assert tools_mod.build_tool_inventory(_rt()) is None


def test_build_inventory_formats(monkeypatch, tmp_path):
    _no_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: _result(json_data=SAMPLE))
    text = tools_mod.build_tool_inventory(_rt())
    assert text is not None
    assert "### management 工具箱" in text
    assert "Project(" in text
    assert "Clip(" in text


# --------------------------------------------------------------------------- #
# 工具清单缓存
# --------------------------------------------------------------------------- #


def test_cache_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mod.ops, "gis_tools_cache_path", lambda: tmp_path / "cache.json")
    ops_mod = tools_mod.ops
    assert ops_mod.read_gis_tools_cache("py1") is None
    ops_mod.write_gis_tools_cache("py1", SAMPLE, now=1000.0)
    data = ops_mod.read_gis_tools_cache("py1", now=1000.0)
    assert data is not None and data["total_tools"] == 3
    assert ops_mod.read_gis_tools_cache("py2", now=1000.0) is None  # 运行时不匹配
    assert ops_mod.read_gis_tools_cache("py1", now=1000.0 + ops_mod.GIS_TOOLS_CACHE_TTL_S + 1) is None


def test_build_inventory_uses_cache(monkeypatch, tmp_path):
    _no_cache(tmp_path, monkeypatch)
    tools_mod.ops.write_gis_tools_cache(_rt().python, SAMPLE)  # 真实时间戳
    called = []
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: called.append(1) or _result(json_data=SAMPLE))
    text = tools_mod.build_tool_inventory(_rt())
    assert text is not None
    assert called == []  # 命中缓存，不再跑子进程


def test_build_inventory_writes_cache(monkeypatch, tmp_path):
    _no_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: _result(json_data=SAMPLE))
    text = tools_mod.build_tool_inventory(_rt())
    assert text is not None
    data = tools_mod.ops.read_gis_tools_cache(_rt().python)
    assert data is not None and data["total_tools"] == 3


def test_build_inventory_bad_result_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mod.ops, "gis_tools_cache_path", lambda: tmp_path / "cache.json")
    monkeypatch.setattr(tools_mod.ops, "list_gis_tools",
                        lambda rt, **k: _result(json_data={"ok": False, "error": "x"}))
    assert tools_mod.build_tool_inventory(_rt()) is None
    assert tools_mod.ops.read_gis_tools_cache(_rt().python) is None


# --------------------------------------------------------------------------- #
# prompt 层
# --------------------------------------------------------------------------- #


def test_system_prompt_without_inventory_unchanged():
    assert system_prompt() == prompt_mod.SYSTEM_PROMPT


def test_system_prompt_with_inventory():
    text = system_prompt("xxx-inventory-xxx")
    assert INVENTORY_MARKER in text
    assert "xxx-inventory-xxx" in text
    assert text.startswith(prompt_mod.SYSTEM_PROMPT)


def test_format_inventory_order_and_cap():
    text = format_tool_inventory(SAMPLE)
    assert text.index("### management") < text.index("### analysis")
    assert text.index("### analysis") < text.index("### conversion")
    line = next(l for l in text.splitlines() if l.startswith("- Project("))
    inner = line[len("- Project("):-1]
    assert len(inner.split(", ")) == MAX_INLINE_PARAMS
    clip = next(l for l in text.splitlines() if l.startswith("- Clip("))
    assert "-> out_feature_class" in clip


# --------------------------------------------------------------------------- #
# Agent.inject_tool_inventory
# --------------------------------------------------------------------------- #


class _Llm:
    def __init__(self):
        self.calls = 0

    def __call__(self, messages, tools):
        self.calls += 1
        return AssistantMessage(content="ok")


def _agent():
    return Agent(_Llm(), _ctx())


def test_inject_fresh_agent():
    agent = _agent()
    assert agent.has_tool_inventory is False
    assert agent.inject_tool_inventory("abc") is True
    assert agent.has_tool_inventory is True
    assert "abc" in agent.history[0]["content"]
    assert INVENTORY_MARKER in agent.history[0]["content"]


def test_inject_idempotent():
    agent = _agent()
    assert agent.inject_tool_inventory("abc") is True
    assert agent.inject_tool_inventory("abc") is False
    assert agent.history[0]["content"].count(INVENTORY_MARKER) == 1


def test_inject_none_noop():
    agent = _agent()
    before = agent.history[0]["content"]
    assert agent.inject_tool_inventory(None) is False
    assert agent.history[0]["content"] == before


def test_inject_after_user_message():
    agent = _agent()
    agent.history.append({"role": "user", "content": "hi"})
    assert agent.inject_tool_inventory("abc") is True
    assert agent.history[0]["role"] == "system"
    assert "abc" in agent.history[0]["content"]
    assert agent.history[1]["role"] == "user"


def test_inject_keeps_system_first_without_duplicate():
    agent = _agent()
    agent.history[0]["content"] = "base" + INVENTORY_MARKER + "old"
    assert agent.inject_tool_inventory("new") is False
    assert "new" not in agent.history[0]["content"]
