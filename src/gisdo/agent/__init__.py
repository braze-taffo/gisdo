"""GISdo Agent：AI 驱动的 GIS 任务执行器。

由 LLM（OpenAI 格式兼容）当大脑，11 个 engine 脚本当手，safety/alignment/preflight/failure
当护栏。三档自主程度可切换。入口见 :class:`gisdo.agent.loop.Agent`。
"""

from gisdo.agent.llm import AssistantMessage, LlmClient, LlmConfig, LlmError, ToolCall
from gisdo.agent.loop import (
    AUTONOMY_AUTONOMOUS,
    AUTONOMY_CONFIRM_EVERY_STEP,
    AUTONOMY_CONFIRM_WRITES,
    Agent,
    AgentCallbacks,
)
from gisdo.agent.tools import (
    ToolContext,
    ToolRegistry,
    build_tool_inventory,
    default_registry,
)

__all__ = [
    "AUTONOMY_AUTONOMOUS",
    "AUTONOMY_CONFIRM_EVERY_STEP",
    "AUTONOMY_CONFIRM_WRITES",
    "Agent",
    "AgentCallbacks",
    "AssistantMessage",
    "LlmClient",
    "LlmConfig",
    "LlmError",
    "ToolCall",
    "ToolContext",
    "ToolRegistry",
    "build_tool_inventory",
    "default_registry",
]
