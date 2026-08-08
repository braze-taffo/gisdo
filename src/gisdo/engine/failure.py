"""结构化失败处理：保留部分输出、分类、提议版本化重试路径。

对应 SKILL.md 的 "Handle failures without cleanup"：

1. 停止后续变更
2. 保留日志与部分输出（**永不删除**）
3. 报告失败的脚本、参数、消息、输出路径
4. 只读诊断
5. 提议新的版本化重试路径并等待

本模块只读地描述失败，绝不清理任何文件。脚本端同样不删除部分输出，
形成 defense-in-depth。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from gisdo.engine.runner import ScriptResult
from gisdo.engine.versioning import versioned_file, versioned_path

# 失败类别
LOCK = "lock"                  # 文件锁 / schema 锁 / 写入方占用
BROKEN_SOURCE = "broken_source"  # 断裂数据源
BLANK_EXPORT = "blank_export"  # PNG 空白或近空白
VALIDATION = "validation"      # 哈希不匹配、计数不符等校验失败
LICENSE = "license"            # 许可 / 扩展不可用
RUNTIME = "runtime"            # arcpy 不可用 / 运行时缺失
OVERWRITE = "overwrite"        # 输出已存在被拒
UNKNOWN = "unknown"


_VERSION_RE = re.compile(r"^(?P<base>.+)_v(?P<version>\d+)_(?P<date>\d{8})$")


@dataclass
class FailureRecord:
    """一次失败的只读记录。"""

    script: str
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    output_path: str | None
    category: str = UNKNOWN
    partial_outputs: list[str] = field(default_factory=list)
    proposed_retry_path: str | None = None
    messages: str = ""

    @classmethod
    def from_result(
        cls,
        result: ScriptResult,
        output_path: str | None = None,
    ) -> FailureRecord:
        messages = ""
        if isinstance(result.json, dict):
            messages = str(result.json.get("messages", "") or "")
        return cls(
            script=result.script,
            args=list(result.args),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            output_path=output_path,
            category=categorize(result),
            partial_outputs=detect_partial_outputs(output_path),
            messages=messages,
        )

    def propose_retry(self) -> str | None:
        """计算并记录下一个版本化重试路径。"""
        self.proposed_retry_path = propose_retry_path(self.output_path)
        return self.proposed_retry_path

    def format_report(self) -> str:
        retry = self.proposed_retry_path or propose_retry_path(self.output_path)
        parts = [
            f"失败脚本：{self.script}",
            f"退出码：{self.returncode}（{'校验失败' if self.returncode == 3 else '运行错误'}）",
            f"类别：{self.category}",
            f"参数：{' '.join(self.args) if self.args else '（无）'}",
            f"输出路径：{self.output_path or '（未指定）'}",
        ]
        if self.partial_outputs:
            parts.append("部分输出（已保留，未清理）：")
            parts.extend(f"  - {p}" for p in self.partial_outputs)
        if self.messages.strip():
            parts.append("arcpy 消息：")
            parts.append("  " + self.messages.strip().replace("\n", "\n  "))
        stderr = self.stderr.strip()
        if stderr:
            parts.append("stderr：")
            parts.append("  " + stderr.replace("\n", "\n  "))
        if retry:
            parts.append(f"建议重试路径：{retry}")
        parts.append("说明：已停止后续变更，部分输出原样保留；请只读诊断后再用新版本重试。")
        return "\n".join(parts)


def categorize(result: ScriptResult) -> str:
    """从返回码与输出文本推断失败类别。"""
    text = "\n".join([
        result.stderr or "",
        result.stdout or "",
        str(result.json or ""),
    ]).lower()

    if "拒绝覆盖" in text or "refusing to overwrite" in text or "fileexistserror" in text:
        return OVERWRITE
    if ".lock" in text or "schema lock" in text or "锁" in text or "locks" in text:
        return LOCK
    if "broken" in text or "断裂" in text:
        return BROKEN_SOURCE
    if "non_white" in text or "blank" in text or "空白" in text or "像素校验" in text:
        return BLANK_EXPORT
    if "license" in text or "not licensed" in text or "授权" in text:
        return LICENSE
    if ("arcpy" in text and "unavailable" in text) or "no usable" in text:
        return RUNTIME
    if result.validation_failed:
        return VALIDATION
    return UNKNOWN


def detect_partial_outputs(output_path: str | None) -> list[str]:
    """尽力列出失败时已产生但保留的文件/目录。永不删除。"""
    if not output_path:
        return []
    path = Path(output_path)
    if not path.exists():
        return []
    if path.is_file():
        return [str(path)]
    if path.is_dir():
        files = sorted([str(p) for p in path.rglob("*") if p.is_file()], key=str.casefold)
        return files[:50]  # 截断，避免超大目录刷屏；其余仍保留在磁盘
    return []


def parse_versioned_name(path: str | Path) -> tuple[str, int, str] | None:
    """从 ``name_vN_YYYYMMDD[.ext]`` 解析 (base, version, date)；不匹配返回 None。"""
    p = Path(path)
    match = _VERSION_RE.match(p.stem)
    if not match:
        return None
    return match.group("base"), int(match.group("version")), match.group("date")


def propose_retry_path(output_path: str | None) -> str | None:
    """给出下一个版本化重试路径。非版本化路径返回 None。"""
    if not output_path:
        return None
    parsed = parse_versioned_name(output_path)
    if parsed is None:
        return None
    base, version, _date = parsed
    parent = Path(output_path).resolve().parent
    suffix = Path(output_path).suffix
    if suffix:
        next_path = versioned_file(parent, base, suffix.lstrip("."), version=version + 1)
    else:
        next_path = versioned_path(parent, base, version=version + 1)
    return str(next_path)
