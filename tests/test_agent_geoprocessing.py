"""run_geoprocessing 工具测试（monkeypatch ops，避免真实 subprocess / arcpy）。"""

import json
import types

import pytest

from gisdo.agent import tools as tools_mod
from gisdo.agent.tools import ToolContext, default_registry
from gisdo.engine import ops
from gisdo.engine.alignment import Alignment
from gisdo.engine.runner import ScriptResult


def _result(returncode=0, stderr="", stdout="", json_data=None, script="geoprocessing.py"):
    return ScriptResult(
        script=script, interpreter="python", args=[], returncode=returncode,
        stdout=stdout, stderr=stderr, duration_s=0.1, json=json_data, json_error=None,
    )


def _rt():
    return types.SimpleNamespace(python="C:/fake/python.exe", is_py2=False)


def _ctx(confirmed=False):
    ctx = ToolContext()
    ctx.modern_runtime = _rt()
    if confirmed:
        al = Alignment()
        al.confirm()
        ctx.confirmed_alignment = al
    return ctx


def _call(name, args, ctx):
    return default_registry().get(name).call(args, ctx)


# --------------------------------------------------------------------------- #
# ops 层
# --------------------------------------------------------------------------- #


def test_gp_toolbox_parses_dotted_name():
    assert ops.gp_toolbox("analysis.Clip") == "analysis"
    assert ops.gp_toolbox("management.Project") == "management"


def test_gp_toolbox_rejects_missing_dot():
    with pytest.raises(ValueError):
        ops.gp_toolbox("Clip")


def test_ops_gp_whitelist_rejected(monkeypatch):
    called = []
    monkeypatch.setattr(ops, "run_script", lambda *a, **k: called.append(a) or _result())
    al = Alignment()
    al.confirm()
    with pytest.raises(ops.SafetyError):
        ops.run_geoprocessing(_rt(), "sa.Reclassify", {"in_raster": "a"}, "out",
                              alignment=al)
    assert called == []


def test_ops_gp_dispatch_args(monkeypatch, tmp_path):
    captured = {}
    def fake_run_script(interpreter, script, args, **kwargs):
        captured["interpreter"] = interpreter
        captured["script"] = script
        captured["args"] = args
        return _result(json_data={"ok": True, "output": "out"})
    monkeypatch.setattr(ops, "run_script", fake_run_script)
    al = Alignment()
    al.confirm()
    out_path = str(tmp_path / "clip.shp")  # 不存在 -> assert_absent 通过
    result = ops.run_geoprocessing(
        _rt(), "analysis.Clip",
        {"in_features": "E:/a.shp", "clip_features": "E:/b.shp"},
        out_path, alignment=al, check_field="Layer",
    )
    assert result.ok
    assert captured["script"] == "geoprocessing.py"
    assert captured["interpreter"] == "C:/fake/python.exe"
    assert "--tool" in captured["args"]
    assert "analysis.Clip" in captured["args"]
    # params 以 JSON 序列化、输出已 resolve
    params_json = captured["args"][captured["args"].index("--params") + 1]
    assert json.loads(params_json)["in_features"] == "E:/a.shp"
    assert captured["args"][captured["args"].index("--output") + 1] == out_path
    assert "--check-field" in captured["args"]


def test_ops_gp_needs_confirmed_alignment(monkeypatch):
    called = []
    monkeypatch.setattr(ops, "run_script", lambda *a, **k: called.append(a) or _result())
    al = Alignment()  # 未确认
    with pytest.raises(ops.SafetyError):
        ops.run_geoprocessing(_rt(), "analysis.Clip", {}, "out", alignment=al)
    assert called == []


# --------------------------------------------------------------------------- #
# tools 层
# --------------------------------------------------------------------------- #


def test_gp_success_dispatches(monkeypatch):
    captured = {}
    def fake_gp(rt, tool, params, output, *, alignment, output_param, check_field, **k):
        captured["tool"] = tool
        captured["params"] = params
        captured["output"] = output
        captured["alignment"] = alignment
        return _result(json_data={"ok": True, "output": output, "validation": {"count": 12}})
    monkeypatch.setattr(tools_mod.ops, "run_geoprocessing", fake_gp)
    ctx = _ctx(confirmed=True)
    out = _call("run_geoprocessing",
                {"tool": "analysis.Clip",
                 "params": {"in_features": "E:/a.shp", "clip_features": "E:/b.shp"},
                 "output": "E:/out/clip_v1.shp"},
                ctx)
    assert captured["tool"] == "analysis.Clip"
    assert captured["params"]["clip_features"] == "E:/b.shp"
    assert captured["output"] == "E:/out/clip_v1.shp"
    assert captured["alignment"] is ctx.confirmed_alignment
    assert "count" in out


def test_gp_whitelist_rejected_no_dispatch(monkeypatch):
    called = []
    monkeypatch.setattr(tools_mod.ops, "run_geoprocessing",
                        lambda *a, **k: called.append(a) or _result())
    out = _call("run_geoprocessing",
                {"tool": "sa.Reclassify", "params": {}, "output": "o"}, _ctx(confirmed=True))
    assert called == []
    assert "白名单" in out


def test_gp_malformed_tool(monkeypatch):
    called = []
    monkeypatch.setattr(tools_mod.ops, "run_geoprocessing",
                        lambda *a, **k: called.append(a) or _result())
    out = _call("run_geoprocessing",
                {"tool": "Clip", "params": {}, "output": "o"}, _ctx(confirmed=True))
    assert called == []
    assert "toolbox.tool" in out


def test_gp_params_not_dict():
    out = _call("run_geoprocessing",
                {"tool": "analysis.Clip", "params": "nope", "output": "o"}, _ctx(confirmed=True))
    assert "params" in out


def test_gp_needs_alignment(monkeypatch):
    called = []
    monkeypatch.setattr(tools_mod.ops, "run_geoprocessing",
                        lambda *a, **k: called.append(a) or _result())
    out = _call("run_geoprocessing",
                {"tool": "analysis.Clip", "params": {}, "output": "o"}, _ctx(confirmed=False))
    assert called == []
    assert "对齐块" in out


def test_gp_alignment_draft():
    reg = default_registry()
    al = reg.get("run_geoprocessing").prepare_alignment(
        {"tool": "analysis.Clip", "params": {"in_features": "E:/a.shp"},
         "output": "E:/out/clip_v1.shp"},
        _ctx(),
    )
    assert al is not None
    assert al.output_location == "E:/out/clip_v1.shp"
    assert "clip_v1.shp" in al.will_create[0]
    assert al.confirmed is False


def test_gp_in_registry():
    reg = default_registry()
    tool = reg.get("run_geoprocessing")
    assert tool is not None
    assert tool.is_write is True
    schema = tool.schema()["function"]
    # output 可省略（默认落到项目地图输出文件夹），不再是必填
    assert schema["parameters"]["required"] == ["tool", "params"]
    assert tool.normalize_args is not None
