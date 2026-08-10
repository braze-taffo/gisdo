"""Agent 循环：推理 -> 调工具 -> 观察结果 -> 再推理，直到给出最终回答。

三档自主程度，运行时可切换：
- ``confirm_writes``（默认）：只读工具自由跑；写操作暂停，经 ``on_confirm`` 弹对齐块等人确认。
- ``autonomous``：全部自动，写操作自动确认（preflight/safety 仍强制，被拒就把错误喂回 LLM）。
- ``confirm_every_step``：每次工具调用前都确认。

循环同步阻塞，设计成在 worker 线程跑（GUI）或直接跑（CLI）。
``chat_fn`` 是 :class:`~gisdo.agent.llm.ChatFn` 协议的任意可调用对象，测试里可传假实现。
"""

from __future__ import annotations

import inspect
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gisdo.agent.llm import ChatFn, LlmCancelled
from gisdo.agent.prompt import (
    INVENTORY_HEADER,
    INVENTORY_MARKER,
    PROJECT_MARKER,
    system_prompt,
)
from gisdo.agent.tools import ToolContext, ToolRegistry, default_registry
from gisdo.engine.alignment import Alignment
from gisdo.engine.runner import RunCancelled

HISTORY_VERSION = 1

AUTONOMY_CONFIRM_WRITES = "confirm_writes"
AUTONOMY_AUTONOMOUS = "autonomous"
AUTONOMY_CONFIRM_EVERY_STEP = "confirm_every_step"
_VALID_AUTONOMY = {AUTONOMY_CONFIRM_WRITES, AUTONOMY_AUTONOMOUS, AUTONOMY_CONFIRM_EVERY_STEP}

DEFAULT_MAX_ITERATIONS = 25


def _accepts(fn: Callable, name: str) -> bool:
    """函数是否接受某关键字参数（含 **kwargs）；签名不可用时保守返回 True。"""
    try:
        params = inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return True
    if name in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def sanitize_history(messages: list[dict]) -> list[dict]:
    """清洗持久化历史，得到可发送给 LLM 的消息列表。

    - 丢弃 ``system`` 消息（工具清单/项目上下文在下次 run 前重注入）。
    - ``tool`` 消息仅当 ``tool_call_id`` 能对上前面未消费的 assistant ``tool_calls``
      才保留（OpenAI 要求 tool 紧跟对应 assistant tool_call）；孤儿 tool 丢弃。
    - 会话被打断时可能残留「有 tool_calls 但无 tool 回复」的 assistant——保留其
      content 文本但删掉未完成的 ``tool_calls``，避免残缺配对。
    """
    clean: list[dict] = []
    pending: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue
        if role == "tool":
            tid = msg.get("tool_call_id")
            if tid in pending:
                pending.discard(tid)
                clean.append(msg)
            continue
        clean.append(msg)
        calls = msg.get("tool_calls")
        if role == "assistant" and calls:
            pending.update(c["id"] for c in (calls or []) if c.get("id"))
            if not pending:
                # 无有效 id 的 tool_calls 无意义，删掉
                msg.pop("tool_calls", None)
    if pending:
        # 结尾残留未消费 tool_calls 的 assistant：删掉 tool_calls 只留文本
        for msg in reversed(clean):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                msg.pop("tool_calls", None)
                msg.pop("reasoning_content", None)
                break
    return clean


@dataclass
class AgentCallbacks:
    """Agent 事件回调，全部有默认空实现。"""

    on_assistant_text: Callable[[str], None] = lambda _text: None
    on_token: Callable[[str], None] = lambda _token: None
    on_tool_start: Callable[[str, dict], None] = lambda _name, _args: None
    on_tool_end: Callable[[str, str], None] = lambda _name, _result: None
    on_confirm: Callable[[str, dict, Alignment | None], bool] = lambda _name, _args, _al: True
    on_ask_user: Callable[[str, list[str]], str | None] = lambda _question, _options: None
    on_error: Callable[[str], None] = lambda _msg: None
    on_info: Callable[[str], None] = lambda _msg: None


def _needs_confirm(tool_is_write: bool, autonomy: str) -> bool:
    if autonomy == AUTONOMY_AUTONOMOUS:
        return False
    if autonomy == AUTONOMY_CONFIRM_EVERY_STEP:
        return True
    return tool_is_write  # confirm_writes


class Agent:
    """单次会话的 Agent。持有对话历史，可多轮 ``run``。"""

    def __init__(
        self,
        chat_fn: ChatFn,
        ctx: ToolContext,
        *,
        registry: ToolRegistry | None = None,
        callbacks: AgentCallbacks | None = None,
        autonomy: str = AUTONOMY_CONFIRM_WRITES,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        system: str | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        if autonomy not in _VALID_AUTONOMY:
            raise ValueError(f"未知自主模式：{autonomy}（可选：{sorted(_VALID_AUTONOMY)}）")
        self.chat_fn = chat_fn
        self.ctx = ctx
        self.registry = registry or default_registry()
        self.callbacks = callbacks or AgentCallbacks()
        self.autonomy = autonomy
        self.max_iterations = max_iterations
        self._cancel = cancel
        self._chat_streams = _accepts(self.chat_fn, "on_token")
        self._system = system or system_prompt()
        self.history: list[dict] = [{"role": "system", "content": self._system}]
        self._injected_tools = False
        # ask_user 工具经 ctx 回访人类；Agent 把它接上自己的回调
        self.ctx.on_ask_user = self.callbacks.on_ask_user

    @property
    def has_tool_inventory(self) -> bool:
        """是否已注入地理处理工具清单（幂等标记）。"""
        return self._injected_tools

    def inject_tool_inventory(self, inventory: str | None) -> bool:
        """把工具清单追加进系统提示词；已注入或清单为空则跳过。返回是否本次注入。"""
        if not inventory:
            return False
        section = "\n\n" + INVENTORY_HEADER + "\n" + inventory
        if not self.history:
            self.history = [{"role": "system", "content": section}]
            self._injected_tools = True
            return True
        if self.history[0].get("role") == "system":
            content = self.history[0]["content"]
            if INVENTORY_MARKER in content:
                return False
            self.history[0]["content"] = content + section
            self._injected_tools = True
            return True
        self.history.insert(0, {"role": "system", "content": section})
        self._injected_tools = True
        return True

    def inject_project_context(self, context: str | None) -> bool:
        """把项目上下文追加进系统提示词；已注入或为空则跳过。返回是否本次注入。"""
        if not context:
            return False
        section = "\n\n" + PROJECT_MARKER + "\n" + context
        content = self.history[0].get("content", "")
        if PROJECT_MARKER in content:
            return False
        self.history[0]["content"] = content + section
        return True

    def set_autonomy(self, autonomy: str) -> None:
        if autonomy not in _VALID_AUTONOMY:
            raise ValueError(f"未知自主模式：{autonomy}")
        self.autonomy = autonomy

    def reset(self) -> None:
        """清空对话历史（保留系统提示词）。"""
        self.history = [{"role": "system", "content": self._system}]

    def save_history(self, path: str | Path) -> None:
        """把历史中除 system 外的消息写入 JSON。自动创建父目录。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        messages = [m for m in self.history if m.get("role") != "system"]
        path.write_text(json.dumps(
            {"version": HISTORY_VERSION, "messages": messages},
            ensure_ascii=False,
        ), encoding="utf-8")

    def load_history(self, messages: list[dict]) -> None:
        """用持久化历史重建对话（保留当前 system，重注入工具清单标记需重新触发）。"""
        clean = sanitize_history(list(messages))
        self.history = [{"role": "system", "content": self._system}] + clean
        self._injected_tools = False

    def _on_token(self, token: str) -> None:
        """流式 token 回调：检查取消，未取消则转发给上层回调。"""
        if self._cancel is not None and self._cancel.is_set():
            raise LlmCancelled("已取消")
        self.callbacks.on_token(token)

    def run(self, user_message: str) -> str:
        """处理一轮用户消息，返回助手最终文本。"""
        self.history.append({"role": "user", "content": user_message})
        for _ in range(self.max_iterations):
            try:
                kwargs: dict = {}
                if self._chat_streams:
                    kwargs["on_token"] = self._on_token
                msg = self.chat_fn(self.history, self.registry.as_openai_tools(), **kwargs)
            except RunCancelled:
                raise  # 取消异常冒泡，不让兜底 on_error 吞掉
            except Exception as exc:  # noqa: BLE001 - LLM 异常统一兜底
                self.callbacks.on_error(f"LLM 调用失败：{type(exc).__name__}: {exc}")
                return f"（LLM 调用失败：{exc}）"
            self.history.append(msg.to_api())
            if not msg.has_tool_calls:
                text = msg.content or ""
                self.callbacks.on_assistant_text(text)
                return text
            # 执行所有工具调用，结果回填
            for tc in msg.tool_calls:
                result = self._execute_tool(tc.name, tc.arguments, tc.id)
                self.history.append({
                    "role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result,
                })
        self.callbacks.on_error(f"达到最大迭代数 {self.max_iterations}，已停止。")
        return f"（已达到最大迭代数 {self.max_iterations}，请缩小任务或重试。）"

    def _execute_tool(self, name: str, arguments: str, call_id: str) -> str:
        try:
            tool = self.registry.get(name)
        except Exception as exc:  # noqa: BLE001
            return f"工具查找异常：{type(exc).__name__}: {exc}"
        if tool is None:
            return f"未知工具：{name}。可用工具：{', '.join(self.registry.names())}"
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            return f"工具 {name} 参数不是合法 JSON：{exc}"
        if not isinstance(args, dict):
            args = {"value": args}

        # 默认输出落点：省略的输出参数补到当前项目地图输出文件夹（对齐块可见补全后路径）
        if tool.normalize_args is not None:
            try:
                tool.normalize_args(args, self.ctx)
            except Exception as exc:  # noqa: BLE001
                return f"补全默认输出路径失败：{type(exc).__name__}: {exc}"

        # 对齐草稿（写操作）
        alignment: Alignment | None = None
        if tool.is_write and tool.prepare_alignment is not None:
            try:
                alignment = tool.prepare_alignment(args, self.ctx)
            except Exception as exc:  # noqa: BLE001
                return f"构造对齐块失败：{type(exc).__name__}: {exc}"

        # 自主门禁（ask_user 本身就是在问人，不再套一层确认）
        if tool.name != "ask_user" and _needs_confirm(tool.is_write, self.autonomy):
            approved = self._ask_confirm(name, args, alignment)
            if not approved:
                self.callbacks.on_info(f"已拒绝工具调用：{name}")
                return f"用户拒绝了此次操作（{name}）。请改方案或与用户确认后重试。"

        # 写操作：确认后回填已确认的对齐块（confirm 后 ops.require_confirmed() 才放行）
        if alignment is not None:
            alignment.confirm()
            self.ctx.confirmed_alignment = alignment
        else:
            self.ctx.confirmed_alignment = None

        self.callbacks.on_tool_start(name, args)
        result = tool.call(args, self.ctx)
        self.callbacks.on_tool_end(name, result)
        return result

    def _ask_confirm(self, name: str, args: dict, alignment: Alignment | None) -> bool:
        summary = json.dumps(args, ensure_ascii=False)
        block = alignment.as_block() if alignment else "（只读操作，无对齐块）"
        self.callbacks.on_info(f"请求确认工具 {name}：\n参数：{summary}\n{block}")
        try:
            return bool(self.callbacks.on_confirm(name, args, alignment))
        except Exception as exc:  # noqa: BLE001
            self.callbacks.on_error(f"确认回调异常：{exc}")
            return False


__all__ = [
    "AUTONOMY_AUTONOMOUS",
    "AUTONOMY_CONFIRM_EVERY_STEP",
    "AUTONOMY_CONFIRM_WRITES",
    "DEFAULT_MAX_ITERATIONS",
    "Agent",
    "AgentCallbacks",
    "sanitize_history",
]
