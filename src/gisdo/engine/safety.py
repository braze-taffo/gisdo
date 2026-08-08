"""安全不变量校验。

集中实现 SKILL.md 的"永不删除/覆盖"族规则。脚本端各自强制同一条规则；
本模块让 GUI/CLI 在派发前先校验，给出更友好的错误，并形成 defense-in-depth。
"""

from __future__ import annotations

from pathlib import Path


class SafetyError(RuntimeError):
    """违反安全不变量。"""


def assert_absent(path: Path | str) -> Path:
    """路径必须不存在；否则拒绝（永不覆盖）。"""
    resolved = Path(path).expanduser().resolve()
    if resolved.exists():
        raise SafetyError(f"拒绝覆盖已存在的输出：{resolved}")
    return resolved


def ensure_parent(path: Path | str) -> Path:
    """确保父目录存在，但不创建目标本身。"""
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def active_locks(*gdb_roots: Path | str) -> list[Path]:
    """列出给定 GDB 根目录下的 ``.lock`` 文件。"""
    locks: list[Path] = []
    for root in gdb_roots:
        if not root:
            continue
        directory = Path(root)
        if directory.is_dir():
            locks.extend(directory.rglob("*.lock"))
    return sorted(set(locks), key=lambda p: str(p).casefold())


def assert_no_active_locks(*gdb_roots: Path | str) -> None:
    """若任何 GDB 含活动锁则拒绝快照。"""
    locks = active_locks(*gdb_roots)
    if locks:
        raise SafetyError(
            "拒绝快照含活动锁的文件 GDB。请先关闭写入方（如 ArcMap/Pro）：\n"
            + "\n".join(f"  - {lock}" for lock in locks)
        )


def assert_no_broken_sources(inventory: dict, *, allow: bool = False) -> None:
    """若 APRX 清单含断裂源则拒绝打包等写操作，除非用户显式放行。"""
    broken_count = int(inventory.get("broken_count", 0) or 0)
    if broken_count and not allow:
        broken = inventory.get("broken", []) or []
        sample = "; ".join(
            f"{item.get('map', '')}/{item.get('name', '')}" for item in broken[:5]
        )
        raise SafetyError(
            f"工程存在 {broken_count} 个断裂数据源，拒绝打包：{sample}"
            + ("…" if broken_count > 5 else "")
            + "\n如需放行，请显式确认 allow_broken。"
        )


def assert_can_overwrite_off(value: bool) -> None:
    """提醒调用方保持 ``arcpy.env.overwriteOutput = False``（脚本内部已设）。"""
    if value:
        raise SafetyError("禁止开启 overwriteOutput；本工作台永不覆盖既有输出。")
