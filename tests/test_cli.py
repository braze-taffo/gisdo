"""CLI 集成测试：preflight 子命令与写命令失败收尾。"""

import json
import sys

import pytest

import gisdo.cli
from gisdo.cli import _AUTONOMY_CHOICES, _finish_write, main
from gisdo.engine.failure import BLANK_EXPORT, LOCK
from gisdo.engine.runner import ScriptResult


def _result(returncode=1, stderr="", stdout="", json_data=None, script="x.py"):
    return ScriptResult(
        script=script,
        interpreter="python",
        args=["a"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_s=0.1,
        json=json_data,
        json_error=None,
    )


# --------------------------------------------------------------------------- #
# preflight 子命令
# --------------------------------------------------------------------------- #


def test_preflight_passes_with_fresh_output(tmp_path, capsys):
    rc = main(["preflight", "--python", sys.executable, "--output", str(tmp_path / "fresh")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "全部通过" in out
    assert "运行时可用" in out
    assert "输出路径不存在" in out


def test_preflight_blocks_when_output_exists(tmp_path, capsys):
    existing = tmp_path / "exists"
    existing.mkdir()
    rc = main(["preflight", "--python", sys.executable, "--output", str(existing)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "阻断" in out
    assert "已存在" in out


def test_preflight_blocks_when_interpreter_missing(tmp_path, capsys):
    rc = main(["preflight", "--python", str(tmp_path / "nope.exe"), "--output", str(tmp_path / "fresh")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "运行时可用" in out


# --------------------------------------------------------------------------- #
# _finish_write 失败收尾
# --------------------------------------------------------------------------- #


def test_finish_write_runtime_failure_prints_report(capsys):
    result = _result(returncode=1, stderr="ERROR 000464: schema lock")
    rc = _finish_write(result, output_path="out_v1_20260101")
    err = capsys.readouterr().err
    assert rc == 1
    assert "失败脚本" in err
    assert "运行错误" in err
    assert "out_v1_20260101" in err
    # 锁类别应被识别（类别字段打印常量值）
    assert LOCK in err
    # 建议重试路径（版本号自增）
    assert "建议重试路径" in err
    assert "v2" in err


def test_finish_write_validation_failure_returns_3(capsys):
    result = _result(returncode=3, stdout="non_white_ratio 0.0", stderr="像素校验失败")
    rc = _finish_write(result, output_path="png_v1_20260101.png")
    err = capsys.readouterr().err
    assert rc == 3
    assert "校验失败" in err
    assert BLANK_EXPORT in err


def test_finish_write_success_delegates_to_report(capsys):
    result = _result(returncode=0, stdout="", json_data={"ok": True, "output": "x"})
    rc = _finish_write(result, output_path="out_v1")
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["ok"] is True


# --------------------------------------------------------------------------- #
# gisdo chat
# --------------------------------------------------------------------------- #


def test_chat_autonomy_mapping():
    assert _AUTONOMY_CHOICES["confirm-writes"] == "confirm_writes"
    assert _AUTONOMY_CHOICES["autonomous"] == "autonomous"
    assert _AUTONOMY_CHOICES["confirm-every-step"] == "confirm_every_step"


def test_chat_invalid_autonomy_rejected():
    with pytest.raises(SystemExit):
        main(["chat", "--autonomy", "bogus"])


def test_chat_missing_config_exits(monkeypatch, capsys):
    from gisdo.config import Settings
    monkeypatch.setattr(gisdo.cli.Settings, "load", lambda: Settings())
    with pytest.raises(SystemExit) as ei:
        main(["chat"])
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "未配置 LLM" in err


# --------------------------------------------------------------------------- #
# gisdo project
# --------------------------------------------------------------------------- #


def _project_store_isolated(tmp_path, monkeypatch):
    import gisdo.project as project_mod
    monkeypatch.setattr(project_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(project_mod, "PROJECTS_DIR", tmp_path / "projects")


def test_project_new_and_list(tmp_path, monkeypatch, capsys):
    _project_store_isolated(tmp_path, monkeypatch)
    assert main(["project", "new", "甲", "--map-output-dir", "E:/map"]) == 0
    out = capsys.readouterr().out
    assert "已创建并设为当前项目：甲" in out

    assert main(["project", "list"]) == 0
    out = capsys.readouterr().out
    assert "甲" in out
    assert "E:/map" in out


def test_project_use_by_name(tmp_path, monkeypatch, capsys):
    _project_store_isolated(tmp_path, monkeypatch)
    main(["project", "new", "a"])
    capsys.readouterr()
    main(["project", "new", "b"])
    capsys.readouterr()

    assert main(["project", "use", "a"]) == 0
    out = capsys.readouterr().out
    assert "已设为当前项目：a" in out


def test_project_rm(tmp_path, monkeypatch, capsys):
    _project_store_isolated(tmp_path, monkeypatch)
    main(["project", "new", "x"])
    capsys.readouterr()
    assert main(["project", "rm", "x"]) == 0
    out = capsys.readouterr().out
    assert "已删除项目：x" in out
    assert main(["project", "list"]) == 0
    assert "x" not in capsys.readouterr().out


def test_project_rm_missing_dies(tmp_path, monkeypatch):
    _project_store_isolated(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        main(["project", "rm", "nope"])


def test_chat_resolve_project_new_project(tmp_path, monkeypatch):
    _project_store_isolated(tmp_path, monkeypatch)
    from argparse import Namespace

    from gisdo.cli import _resolve_project
    proj = _resolve_project(Namespace(new_project="n", project_dir="", map_output_dir="", project=None))
    assert proj.name == "n"
    # 再解析同名应报错
    with pytest.raises(SystemExit):
        _resolve_project(Namespace(new_project="n", project_dir="", map_output_dir="", project=None))


def test_chat_resolve_project_no_active_returns_none(tmp_path, monkeypatch):
    _project_store_isolated(tmp_path, monkeypatch)
    from argparse import Namespace

    from gisdo.cli import _resolve_project
    assert _resolve_project(Namespace(new_project=None, project=None, project_dir="", map_output_dir="")) is None
