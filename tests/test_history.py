"""对话历史持久化与清洗测试：sanitize_history / Agent.save/load_history。"""

import json
import types

from gisdo.agent.llm import AssistantMessage, ToolCall
from gisdo.agent.loop import Agent, AgentCallbacks, sanitize_history
from gisdo.agent.prompt import PROJECT_MARKER
from gisdo.agent.tools import ToolContext
from gisdo.engine.runner import ScriptResult


def _result(json_data=None):
    return ScriptResult(script="x.py", interpreter="python", args=[], returncode=0,
                        stdout="", stderr="", duration_s=0.1, json=json_data, json_error=None)


def _ctx():
    ctx = ToolContext()
    ctx.modern_runtime = types.SimpleNamespace(python="C:/fake/python.exe", is_py2=False)
    return ctx


def _msg(role, **kw):
    return {"role": role, **kw}


class _FakeLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, messages, tools, *, on_token=None):
        self.calls += 1
        return self.responses.pop(0)


def _tc(name, args, id_="c1"):
    return AssistantMessage(tool_calls=[ToolCall(id=id_, name=name,
                                                 arguments=json.dumps(args))])


def _final(text):
    return AssistantMessage(content=text)


# --------------------------------------------------------------------------- #
# sanitize_history
# --------------------------------------------------------------------------- #


def test_sanitize_drops_system():
    clean = sanitize_history([
        _msg("system", content="sys"),
        _msg("user", content="hi"),
    ])
    assert clean == [{"role": "user", "content": "hi"}]


def test_sanitize_keeps_valid_tool_pair():
    clean = sanitize_history([
        _msg("assistant", content=None,
             tool_calls=[{"id": "c1", "type": "function",
                          "function": {"name": "inspect_aprx", "arguments": "{}"}}]),
        _msg("tool", tool_call_id="c1", name="inspect_aprx", content="ok"),
    ])
    assert len(clean) == 2
    assert clean[1]["role"] == "tool"


def test_sanitize_drops_orphan_tool():
    clean = sanitize_history([
        _msg("tool", tool_call_id="nope", name="x", content="orphan"),
        _msg("user", content="hi"),
    ])
    assert clean == [{"role": "user", "content": "hi"}]


def test_sanitize_strips_unfinished_tool_calls():
    clean = sanitize_history([
        _msg("assistant", content="进行中…", reasoning_content="未完成思考",
             tool_calls=[{"id": "c1", "type": "function",
                          "function": {"name": "x", "arguments": "{}"}}]),
    ])
    assert clean == [{"role": "assistant", "content": "进行中…"}]
    assert "tool_calls" not in clean[0]
    assert "reasoning_content" not in clean[0]


def test_sanitize_drops_tool_after_all_consumed():
    clean = sanitize_history([
        _msg("assistant", content=None, tool_calls=[
            {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]),
        _msg("tool", tool_call_id="c1", name="x", content="ok"),
        _msg("tool", tool_call_id="stray", name="x", content="orphan"),
    ])
    assert len(clean) == 2
    assert clean[-1]["role"] == "tool"
    assert clean[-1]["tool_call_id"] == "c1"


# --------------------------------------------------------------------------- #
# Agent.save_history / load_history
# --------------------------------------------------------------------------- #


def test_save_history_excludes_system(tmp_path):
    agent = Agent(_FakeLlm([_final("ok")]), _ctx())
    agent.run("hi")
    out = tmp_path / "history.json"
    agent.save_history(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert all(m["role"] != "system" for m in data["messages"])
    assert data["messages"][0]["role"] == "user"


def test_load_history_restores_and_allows_continue():
    # 第一轮跑出 用户->工具->最终答复
    rec = AgentCallbacks()
    fake1 = _FakeLlm([
        _tc("inspect_aprx", {"project": "x.aprx"}),
        _final("完成"),
    ])
    agent1 = Agent(fake1, _ctx(), callbacks=rec)
    agent1.run("检查工程")
    saved = [dict(m) for m in agent1.history if m.get("role") != "system"]

    # 新 Agent 加载后继续跑
    fake2 = _FakeLlm([_final("继续")])
    agent2 = Agent(fake2, _ctx())
    agent2.load_history(saved)
    assert [m["role"] for m in agent2.history] == ["system", "user", "assistant", "tool", "assistant"]
    assert agent2.has_tool_inventory is False
    ret = agent2.run("再来")
    assert ret == "继续"


def test_reset_then_save_writes_empty(tmp_path):
    agent = Agent(_FakeLlm([_final("ok")]), _ctx())
    agent.run("hi")
    agent.reset()
    out = tmp_path / "history.json"
    agent.save_history(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["messages"] == []


def test_inject_project_context_idempotent():
    agent = Agent(_FakeLlm([_final("ok")]), _ctx())
    assert agent.inject_project_context("项目名称：测试") is True
    assert agent.inject_project_context("项目名称：测试") is False
    content = agent.history[0]["content"]
    assert content.count(PROJECT_MARKER) == 1
    assert "项目名称：测试" in content


def test_load_history_keeps_system_and_marker():
    agent = Agent(_FakeLlm([_final("ok")]), _ctx())
    agent.inject_project_context("项目名称：测试")
    marker_content = agent.history[0]["content"]

    agent2 = Agent(_FakeLlm([_final("ok")]), _ctx())
    agent2.load_history([_msg("user", content="hi")])
    # 新 Agent 的 system 不含旧项目上下文（由调用方重新注入）
    assert PROJECT_MARKER not in agent2.history[0]["content"]
    # 但重新注入后追加正确
    assert agent2.inject_project_context("项目名称：测试") is True
    assert agent2.history[0]["content"] == marker_content
