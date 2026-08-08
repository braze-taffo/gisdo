"""写前预检：把分散的安全校验聚合成结构化报告。

在对齐确认后、派发写操作前调用，得到一份可展示的通过/阻断清单。
不替代脚本端的校验（defense-in-depth），只是更早、更友好地暴露问题。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gisdo.engine.runtime import Runtime
from gisdo.engine.safety import active_locks


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    @property
    def status(self) -> str:
        return "通过" if self.passed else "阻断"


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def format(self) -> str:
        lines = [f"预检：{'全部通过' if self.ok else f'{len(self.blockers)} 项阻断'}"]
        for c in self.checks:
            lines.append(f"  [{c.status}] {c.name}：{c.detail}")
        return "\n".join(lines)


def preflight(
    *,
    runtime: Runtime | None = None,
    output_path: str | Path | None = None,
    inventory: dict | None = None,
    gdb_roots: list[str] | None = None,
    allow_broken: bool = False,
) -> PreflightReport:
    """运行一组只读预检，返回 :class:`PreflightReport`。"""
    checks: list[Check] = []

    # 运行时可用
    if runtime is not None:
        ok = bool(runtime.python) and Path(runtime.python).is_file()
        checks.append(Check(
            name="运行时可用",
            passed=ok,
            detail=runtime.python if ok else f"解释器不存在或未指定：{runtime.python}",
        ))

    # 输出路径不存在
    if output_path is not None:
        resolved = Path(output_path)
        exists = resolved.exists()
        checks.append(Check(
            name="输出路径不存在",
            passed=not exists,
            detail=str(resolved) if not exists else f"已存在，拒绝覆盖：{resolved}",
        ))

    # 无活动锁
    if gdb_roots:
        roots = [Path(r) for r in gdb_roots if r]
        locks = active_locks(*roots)
        checks.append(Check(
            name="无活动 GDB 锁",
            passed=not locks,
            detail="无锁" if not locks else f"{len(locks)} 个锁文件，请关闭写入方",
        ))

    # 无断裂源
    if inventory is not None:
        broken_count = int(inventory.get("broken_count", 0) or 0)
        checks.append(Check(
            name="无断裂源",
            passed=allow_broken or broken_count == 0,
            detail=f"{broken_count} 个断裂源" + ("（已显式放行）" if allow_broken and broken_count else ""),
        ))

    return PreflightReport(checks=checks)
