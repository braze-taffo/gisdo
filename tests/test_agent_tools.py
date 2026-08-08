"""Agent 工具 handler 测试（monkeypatch ops，避免真实 subprocess）。"""

import types

from gisdo.agent import tools as tools_mod
from gisdo.agent.tools import ToolContext, default_registry
from gisdo.engine.alignment import Alignment
from gisdo.engine.runner import ScriptResult


def _result(returncode=0, stderr="", stdout="", json_data=None, script="x.py"):
    return ScriptResult(
        script=script, interpreter="python", args=[], returncode=returncode,
        stdout=stdout, stderr=stderr, duration_s=0.1, json=json_data, json_error=None,
    )


def _rt():
    return types.SimpleNamespace(python="C:/fake/python.exe", is_py2=False)


def _ctx(*, modern=True, arcmap=False, confirmed=False):
    ctx = ToolContext()
    if modern:
        ctx.modern_runtime = _rt()
    if arcmap:
        ctx.arcmap_runtime = types.SimpleNamespace(python="C:/Python27/arcmap.exe", is_py2=True)
    if confirmed:
        al = Alignment()
        al.confirm()
        ctx.confirmed_alignment = al
    return ctx


def _call(name, args, ctx):
    reg = default_registry()
    return reg.get(name).call(args, ctx)


def test_inspect_aprx_ok(monkeypatch):
    monkeypatch.setattr(tools_mod.ops, "inspect_aprx",
                        lambda *a, **k: _result(json_data={"project": "x", "maps_count": 2}))
    out = _call("inspect_aprx", {"project": "x.aprx"}, _ctx())
    assert "maps_count" in out
    assert "2" in out


def test_inspect_aprx_failure_returns_failure_record(monkeypatch):
    monkeypatch.setattr(tools_mod.ops, "inspect_aprx",
                        lambda *a, **k: _result(returncode=1, stderr="ERROR 000464: schema lock"))
    out = _call("inspect_aprx", {"project": "x.aprx"}, _ctx())
    assert "失败脚本" in out
    assert "lock" in out.lower() or "锁" in out


def test_inspect_aprx_missing_arg():
    out = _call("inspect_aprx", {}, _ctx())
    assert "缺少必填参数" in out


def test_inspect_aprx_no_runtime():
    out = _call("inspect_aprx", {"project": "x.aprx"}, _ctx(modern=False))
    assert "运行时" in out


def test_extract_data_needs_alignment(monkeypatch):
    called = []
    monkeypatch.setattr(tools_mod.ops, "extract_data", lambda *a, **k: called.append(k) or _result(json_data={"ok": True}))
    out = _call("extract_data", {"project": "x.aprx", "output_dir": "out_v1"}, _ctx(confirmed=False))
    assert "对齐块" in out
    assert called == []  # 未确认就不该派发


def test_extract_data_with_alignment(monkeypatch):
    captured = {}
    def fake_extract(rt, project, out, *, alignment, **k):
        captured["alignment"] = alignment
        captured["project"] = project
        return _result(json_data={"output_dir": out})
    monkeypatch.setattr(tools_mod.ops, "extract_data", fake_extract)
    ctx = _ctx(confirmed=True)
    out = _call("extract_data", {"project": "x.aprx", "output_dir": "out_v1"}, ctx)
    assert captured["alignment"] is ctx.confirmed_alignment
    assert captured["project"] == "x.aprx"
    assert "output_dir" in out


def test_render_classified_breaks_to_options(monkeypatch):
    captured = {}
    def fake_render(input_json, out_png, options, *, alignment, **k):
        captured["breaks"] = options.breaks
        captured["out"] = out_png
        return _result(json_data={"png": out_png})
    monkeypatch.setattr(tools_mod.ops, "render_classified", fake_render)
    ctx = _ctx(confirmed=True)
    out = _call("render_classified",
                {"input_json": "lines.json", "output_png": "out.png", "breaks": [0, 5, 10]}, ctx)
    assert captured["breaks"] == [0.0, 5.0, 10.0]
    assert captured["out"] == "out.png"
    assert "out.png" in out


def test_render_classified_bad_breaks():
    out = _call("render_classified",
                {"input_json": "lines.json", "output_png": "out.png", "breaks": []}, _ctx(confirmed=True))
    assert "breaks" in out


def test_preflight(monkeypatch):
    monkeypatch.setattr(tools_mod.ops, "inspect_aprx",
                        lambda *a, **k: _result(json_data={"data_sources": [], "broken_count": 0}))
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = _call("preflight", {"project": "x.aprx", "output": os.path.join(d, "fresh")}, _ctx())
    assert ("通过" in out) or ("阻断" in out)


def test_list_dir(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    out = _call("list_dir", {"path": str(tmp_path)}, _ctx())
    assert "a.txt" in out
    assert "sub" in out


def test_read_file(tmp_path):
    f = tmp_path / "m.json"
    f.write_text('{"ok": true}', encoding="utf-8")
    out = _call("read_file", {"path": str(f)}, _ctx())
    assert "ok" in out


def test_read_file_missing():
    out = _call("read_file", {"path": "C:/nope/none.json"}, _ctx())
    assert "不是文件" in out


def test_unknown_tool_returns_error():
    reg = default_registry()
    out = reg.get("does_not_exist")
    assert out is None


def test_prepare_alignment_extract():
    reg = default_registry()
    tool = reg.get("extract_data")
    al = tool.prepare_alignment({"project": "x.aprx", "output_dir": "out_v1"}, _ctx())
    assert al is not None
    assert "out_v1" in al.output_location
    assert al.confirmed is False  # 草稿未确认


# --------------------------------------------------------------------------- #
# ask_user
# --------------------------------------------------------------------------- #


def _ask_ctx(answer):
    ctx = _ctx()
    ctx.on_ask_user = lambda q, o: answer
    return ctx


def test_ask_user_returns_answer():
    out = _call("ask_user", {"question": "用哪个？", "options": ["A", "B"]}, _ask_ctx("B"))
    assert "用户回答：B" in out


def test_ask_user_passes_question_and_options():
    captured = {}
    ctx = _ctx()
    ctx.on_ask_user = lambda q, o: captured.update(q=q, o=o) or "A"
    _call("ask_user", {"question": "哪个方案？", "options": ["A", "B", "C"]}, ctx)
    assert captured["q"] == "哪个方案？"
    assert captured["o"] == ["A", "B", "C"]


def test_ask_user_default_no_frontend():
    out = _call("ask_user", {"question": "q", "options": ["A"]}, _ctx())
    assert "用户未回答" in out


def test_ask_user_no_options_free_text():
    out = _call("ask_user", {"question": "请描述范围"}, _ask_ctx("全要素"))
    assert "用户回答：全要素" in out


def test_ask_user_missing_question():
    out = _call("ask_user", {}, _ctx())
    assert "缺少必填参数" in out


def test_ask_user_bad_options_type():
    out = _call("ask_user", {"question": "q", "options": "not-a-list"}, _ctx())
    assert "options" in out


def test_ask_user_caps_options_at_6():
    captured = {}
    ctx = _ctx()
    ctx.on_ask_user = lambda q, o: captured.update(o=o) or "x"
    _call("ask_user", {"question": "q", "options": [str(i) for i in range(10)]}, ctx)
    assert len(captured["o"]) == 6


def test_ask_user_read_only_in_registry():
    tool = default_registry().get("ask_user")
    assert tool is not None
    assert tool.is_write is False
    schema = tool.schema()["function"]
    assert schema["parameters"]["required"] == ["question"]


# --------------------------------------------------------------------------- #
# 项目上下文注入 + 默认输出落点
# --------------------------------------------------------------------------- #


def _proj(**kw):
    defaults = {"name": "测试", "project_dir": "E:/proj", "map_output_dir": "E:/map"}
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_project_dir_missing_raises():
    try:
        ToolContext().project_dir()
        assert False, "应抛 ToolError"
    except tools_mod.ToolError as exc:
        assert "项目" in str(exc)


def test_map_output_dir_missing_raises():
    try:
        ToolContext().map_output_dir()
        assert False, "应抛 ToolError"
    except tools_mod.ToolError as exc:
        assert "地图输出" in str(exc)


def test_project_dir_returns_value():
    ctx = _ctx()
    ctx.project = _proj()
    assert ctx.project_dir() == "E:/proj"
    assert ctx.map_output_dir() == "E:/map"


def test_normalize_extract_default_output(monkeypatch, tmp_path):
    # normalize 在 loop 层调用后补齐 output_dir（monkeypatch ops 捕获实际传参）
    captured = {}
    monkeypatch.setattr(tools_mod.ops, "extract_data",
                        lambda *a, **k: captured.update(args=a) or _result())
    ctx = _ctx(confirmed=True)
    ctx.project = _proj(map_output_dir=str(tmp_path / "map"))
    reg = default_registry()
    args = {"project": "x.aprx"}
    reg.get("extract_data").normalize_args(args, ctx)
    out = reg.get("extract_data").call(args, ctx)
    assert "缺少必填参数" not in out
    out_dir = captured["args"][2]
    assert str(tmp_path / "map") in out_dir
    assert "_v1_" in out_dir  # 版本化


def test_normalize_gp_default_output(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(tools_mod.ops, "run_geoprocessing",
                        lambda *a, **k: captured.update(args=a) or _result(json_data={"ok": True}))
    ctx = _ctx(confirmed=True)
    ctx.project = _proj(map_output_dir=str(tmp_path / "map"))
    reg = default_registry()
    args = {"tool": "analysis.Clip",
            "params": {"in_features": "a.shp", "clip_features": "b.shp"}}
    reg.get("run_geoprocessing").normalize_args(args, ctx)
    out = reg.get("run_geoprocessing").call(args, ctx)
    assert "缺少必填参数" not in out
    output = captured["args"][3]
    assert str(tmp_path / "map") in output


def test_normalize_explicit_output_wins(monkeypatch):
    captured = {}
    monkeypatch.setattr(tools_mod.ops, "extract_data",
                        lambda *a, **k: captured.update(args=a) or _result())
    ctx = _ctx(confirmed=True)
    ctx.project = _proj()
    _call("extract_data", {"project": "x.aprx", "output_dir": "E:/explicit"}, ctx)
    assert captured["args"][2] == "E:/explicit"


def test_normalize_no_project_falls_through_to_handler(monkeypatch):
    # 无项目时 normalize 抛 ToolError，handler 的 _require 兜底
    out = _call("extract_data", {"project": "x.aprx"}, _ctx(confirmed=True))
    assert "地图输出" in out or "缺少必填参数" in out


def test_alignment_reflects_filled_output(tmp_path):
    reg = default_registry()
    ctx = _ctx()
    ctx.project = _proj(map_output_dir=str(tmp_path / "map"))
    args = {"project": "x.aprx"}
    reg.get("extract_data").normalize_args(args, ctx)
    al = reg.get("extract_data").prepare_alignment(args, ctx)
    assert str(tmp_path / "map") in al.output_location
