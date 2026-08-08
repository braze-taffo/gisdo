"""从可能夹杂 GP 消息/警告的 stdout 中提取末尾 JSON。

引擎脚本均以 ``print(json.dumps(..., indent=2))`` 结尾，JSON 是最后一段输出，
起始行恰好为 ``{``。但 arcpy 在独立运行时会向前面的 stdout 写入 GP 消息，
因此整体 ``json.loads`` 常常失败。这里先尝试整体解析，失败则从最后一个
独占 ``{`` 的行起解析，逐个回退直到成功。
"""

from __future__ import annotations

import json
from typing import Any


class JsonParseError(ValueError):
    """无法从输出中提取 JSON。"""

    def __init__(self, message: str, stdout: str):
        super().__init__(message)
        self.stdout = stdout


def extract_trailing_json(text: str) -> Any:
    """返回 ``text`` 末尾的 JSON 对象；失败抛 :class:`JsonParseError`。"""
    if text is None:
        raise JsonParseError("stdout 为空", "")
    stripped = text.strip()
    if not stripped:
        raise JsonParseError("stdout 为空", text)

    # 快路径：整体就是 JSON。
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 找所有“独占 { 的行”作为候选起点，从最后往前试。
    lines = text.splitlines()
    candidates = [index for index, line in enumerate(lines) if line.strip() == "{"]
    last_error: json.JSONDecodeError | None = None
    for index in reversed(candidates):
        chunk = "\n".join(lines[index:])
        try:
            return json.loads(chunk)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

    snippet = stripped[-200:] if len(stripped) > 200 else stripped
    raise JsonParseError(
        f"无法从输出末尾解析 JSON：{last_error}. 末尾片段：{snippet!r}",
        text,
    )
