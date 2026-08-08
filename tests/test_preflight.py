"""写前预检测试。"""

from pathlib import Path

from gisdo.engine.preflight import preflight
from gisdo.engine.runtime import Runtime


def test_all_pass(tmp_path: Path):
    rt = Runtime(python=sys_executable())
    out = tmp_path / "new_output"
    report = preflight(runtime=rt, output_path=out)
    assert report.ok
    assert not report.blockers


def test_runtime_missing(tmp_path: Path):
    rt = Runtime(python=str(tmp_path / "nope.exe"))
    report = preflight(runtime=rt, output_path=tmp_path / "out")
    assert not report.ok
    assert any("运行时" in c.name for c in report.blockers)


def test_output_exists_blocks(tmp_path: Path):
    existing = tmp_path / "exists"
    existing.mkdir()
    report = preflight(output_path=existing)
    assert not report.ok
    assert any("输出路径" in c.name for c in report.blockers)


def test_active_locks_block(tmp_path: Path):
    gdb = tmp_path / "data.gdb"
    gdb.mkdir()
    (gdb / "f.lock").write_bytes(b"")
    report = preflight(gdb_roots=[str(gdb)])
    assert not report.ok
    assert any("锁" in c.name for c in report.blockers)


def test_broken_sources_block():
    inventory = {"broken_count": 3, "broken": [{"name": "x"}]}
    report = preflight(inventory=inventory)
    assert not report.ok
    assert any("断裂" in c.name for c in report.blockers)


def test_broken_sources_allowed():
    inventory = {"broken_count": 3, "broken": [{"name": "x"}]}
    report = preflight(inventory=inventory, allow_broken=True)
    assert report.ok


def test_format_string(tmp_path: Path):
    report = preflight(output_path=tmp_path / "new")
    text = report.format()
    assert "预检" in text
    assert "通过" in text


def sys_executable() -> str:
    import sys
    return sys.executable
