"""OpenAI 格式兼容的 LLM 客户端 + 消息类型。

类型（:class:`AssistantMessage` / :class:`ToolCall`）是与 SDK 解耦的纯数据对象，
便于在测试里用假客户端驱动 :mod:`gisdo.agent.loop`，无需联网。

任何兼容 OpenAI Chat Completions + function-calling 的 endpoint 都可接：
DeepSeek / Qwen / Kimi / OpenAI / 本地 Ollama（``/v1/chat/completions``）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from gisdo.engine.runner import RunCancelled


class JsonParseError(ValueError):
    """工具调用参数 JSON 解析失败。"""


class LlmCancelled(RunCancelled):
    """LLM 流式调用期间被用户取消。"""


@dataclass
class ToolCall:
    """一次工具调用（对应 OpenAI 的 ``tool_calls`` 条目）。"""

    id: str
    name: str
    arguments: str  # 原始 JSON 字符串，未解析

    def parsed_args(self) -> dict:
        """解析 arguments 为 dict；空串视为空 dict。"""
        if not self.arguments:
            return {}
        try:
            value = json.loads(self.arguments)
        except json.JSONDecodeError as exc:
            raise JsonParseError(f"工具 {self.name} 参数不是合法 JSON：{exc}") from exc
        return value if isinstance(value, dict) else {"value": value}

    def to_api(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass
class AssistantMessage:
    """LLM 的一次回复：要么是最终文本（content），要么含工具调用（tool_calls）。"""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def to_api(self) -> dict:
        """转成 OpenAI 消息格式，用于回填对话历史。"""
        msg: dict[str, Any] = {"role": "assistant"}
        if self.content:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_api() for tc in self.tool_calls]
        return msg


class ChatFn(Protocol):
    """``chat(messages, tools, *, on_token=None) -> AssistantMessage`` 的协议。

    ``on_token`` 为可选回调：传入时实现应按流式方式消费，逐 token 调用它；
    不传则保持一次性返回。keyword-only 且带默认值，向后兼容旧调用方。
    """

    def __call__(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        on_token: Callable[[str], None] | None = None,
    ) -> AssistantMessage: ...


@dataclass
class LlmConfig:
    """LLM 端点配置。``api_key`` 为空时回退到 ``GISDO_API_KEY`` 环境变量。"""

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = 120.0

    def resolved_api_key(self) -> str:
        return self.api_key or os.environ.get("GISDO_API_KEY", "")


class LlmError(RuntimeError):
    """LLM 调用失败。"""


def _assistant_from_message(message) -> AssistantMessage:
    """同步分支：从 ``choices[0].message`` 构造 :class:`AssistantMessage`。"""
    tool_calls = [
        ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "")
        for tc in (getattr(message, "tool_calls", None) or [])
    ]
    return AssistantMessage(content=message.content, tool_calls=tool_calls)


def _consume_stream(stream, on_token: Callable[[str], None]) -> AssistantMessage:
    """流式分支：消费 chunks，逐 content 片段回调 ``on_token``，累积 tool_calls。

    OpenAI 流式响应中 ``tool_calls`` 按 index 分片：``id``/``name`` 只在首个片段
    出现整值，``arguments`` 是跨片段增量拼接；``delta``/``delta.function`` 可能为
    ``None``，逐层判空。
    """
    content_parts: list[str] = []
    pending: dict[int, dict] = {}  # index -> {"id", "name", "arguments"}
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue
        if delta.content:
            on_token(delta.content)
            content_parts.append(delta.content)
        for tc in getattr(delta, "tool_calls", None) or []:
            entry = pending.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                entry["id"] = tc.id
            fn = tc.function
            if fn is not None:
                if fn.name:
                    entry["name"] = fn.name
                if fn.arguments:
                    entry["arguments"] += fn.arguments
    tool_calls = [
        ToolCall(id=e["id"], name=e["name"], arguments=e["arguments"])
        for _i, e in sorted(pending.items())
    ]
    content = "".join(content_parts)
    return AssistantMessage(content=content or None, tool_calls=tool_calls)


class LlmClient:
    """OpenAI 兼容客户端。``openai`` SDK 懒加载，缺失时友好报错。"""

    def __init__(self, config: LlmConfig) -> None:
        self.config = config

    def _client(self):
        try:
            import openai  # 懒加载，可选依赖
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise LlmError(
                "未安装 openai SDK。请运行 pip install gisdo[ai] 或 pip install openai。"
            ) from exc
        key = self.config.resolved_api_key()
        if not self.config.base_url:
            raise LlmError("未配置 LLM base_url。请在设置中填写 OpenAI 兼容端点。")
        if not key:
            raise LlmError("未配置 api_key（也未设置 GISDO_API_KEY 环境变量）。")
        return openai.OpenAI(base_url=self.config.base_url, api_key=key, timeout=self.config.timeout)

    def chat(self, messages: list[dict], tools: list[dict], *,
             on_token: Callable[[str], None] | None = None) -> AssistantMessage:
        """调用 Chat Completions，返回 :class:`AssistantMessage`。

        传 ``on_token`` 时走 ``stream=True``，逐 content 片段回调；否则一次性返回。
        """
        if not self.config.model:
            raise LlmError("未配置 model。请在设置中填写模型名。")
        client = self._client()
        kwargs = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools or None,
            "tool_choice": "auto" if tools else None,
        }
        try:
            if on_token is not None:
                return _consume_stream(
                    client.chat.completions.create(**kwargs, stream=True), on_token
                )
            resp = client.chat.completions.create(**kwargs)
            return _assistant_from_message(resp.choices[0].message)
        except RunCancelled:
            raise  # 取消异常不得被包成 LlmError
        except Exception as exc:  # SDK 抛各类异常（含流迭代期错误），统一封装
            raise LlmError(f"LLM 调用失败：{type(exc).__name__}: {exc}") from exc


__all__ = [
    "AssistantMessage",
    "ChatFn",
    "JsonParseError",
    "LlmCancelled",
    "LlmClient",
    "LlmConfig",
    "LlmError",
    "ToolCall",
]
