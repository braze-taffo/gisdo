"""安全不变量测试。"""

from pathlib import Path

from gisdo.engine.safety import (
    SafetyError,
    active_locks,
    assert_absent,
    assert_no_active_locks,
    assert_no_broken_sources,
)


def test_assert_absent_blocks_existing(tmp_path: Path):
    existing = tmp_path / "out"
    existing.mkdir()
    try:
        assert_absent(existing)
    except SafetyError:
        return
    raise AssertionError("应拒绝已存在路径")


def test_assert_absent_passes_for_new(tmp_path: Path):
    resolved = assert_absent(tmp_path / "new")
    assert resolved == (tmp_path / "new").resolve()


def test_active_locks_detected(tmp_path: Path):
    gdb = tmp_path / "data.gdb"
    gdb.mkdir()
    (gdb / "a.bin").write_bytes(b"x")
    (gdb / "f.lock").write_bytes(b"")
    locks = active_locks(gdb)
    assert any(p.name == "f.lock" for p in locks)


def test_assert_no_active_locks_blocks(tmp_path: Path):
    gdb = tmp_path / "data.gdb"
    gdb.mkdir()
    (gdb / "f.lock").write_bytes(b"")
    try:
        assert_no_active_locks(gdb)
    except SafetyError:
        return
    raise AssertionError("应拒绝含活动锁的 GDB")


def test_assert_no_active_locks_passes_when_clean(tmp_path: Path):
    gdb = tmp_path / "data.gdb"
    gdb.mkdir()
    (gdb / "a.bin").write_bytes(b"x")
    assert_no_active_locks(gdb)  # 不抛即通过


def test_broken_sources_block_packaging():
    inventory = {"broken_count": 2, "broken": [{"map": "m", "name": "a"}, {"map": "m", "name": "b"}]}
    try:
        assert_no_broken_sources(inventory)
    except SafetyError:
        return
    raise AssertionError("应拒绝含断裂源的打包")


def test_broken_sources_allowed_when_flag():
    inventory = {"broken_count": 1, "broken": [{"map": "m", "name": "a"}]}
    assert_no_broken_sources(inventory, allow=True)  # 不抛即通过


def test_no_broken_sources_passes():
    assert_no_broken_sources({"broken_count": 0, "broken": []})
