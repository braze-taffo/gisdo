"""Agent 循环测试：FakeLlm 驱动，验证多步调用、三档自主、最大迭代保护。"""

import json
import threading
import types

import pytest

from gisdo.agent import tools as tools_mod
from gisdo.agent.llm import AssistantMessage, LlmCancelled, ToolCall
from gisdo.agent.loop import (
    AUTONOMY_AUTONOMOUS,
    AUTONOMY_CONFIRM_EVERY_STEP,
    AUTONOMY_CONFIRM_WRITES,
    Agent,
    AgentCallbacks,
)
from gisdo.agent.tools import ToolContext
from gisdo.engine.runner import ScriptResult


def _result(returncode=0, stderr="", stdout="", json_data=None):
    return ScriptResult(script="x.py", interpreter="python", args=[], returncode=returncode,
                        stdout=stdout, stderr=stderr, duration_s=0.1, json=json_data, json_error=None)


def _tc(name, args, id_="c1"):
    return AssistantMessage(tool_calls=[ToolCall(id=id_, name=name, arguments=json.dumps(args))])


def _final(text):
    return AssistantMessage(content=text)


class FakeLlm:
    """按脚本顺序返回回复；收到 on_token 时对 content 逐字符模拟流式。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, messages, tools, *, on_token=None):
        self.calls += 1
        if not self.responses:
            raise AssertionError("FakeLlm 脚本耗尽")
        msg = self.responses[0]
        if on_token is not None and msg.content:
            for ch in msg.content:
                on_token(ch)
        return self.responses.pop(0)


class RepeatLlm:
    """总是返回同一条工具调用（用于测最大迭代保护）。"""

    def __init__(self, msg):
        self.msg = msg
        self.calls = 0

    def __call__(self, messages, tools, *, on_token=None):
        self.calls += 1
        return self.msg


class Rec:
    """记录所有回调。"""

    def __init__(self, confirm=True, ask=None):
        self.text = []
        self.tokens = []
        self.starts = []
        self.ends = []
        self.confirms = []
        self.asks = []
        self.errors = []
        self.infos = []
        self._confirm = confirm
        self._ask = ask

    def cb(self):
        return AgentCallbacks(
            on_assistant_text=self.text.append,
            on_token=self.tokens.append,
            on_tool_start=lambda n, a: self.starts.append((n, a)),
            on_tool_end=lambda n, r: self.ends.append((n, r)),
            on_confirm=self._on_confirm,
            on_ask_user=self._on_ask,
            on_error=self.errors.append,
            on_info=self.infos.append,
        )

    def _on_confirm(self, name, args, alignment):
        self.confirms.append((name, args, alignment))
        return self._confirm

    def _on_ask(self, question, options):
        self.asks.append((question, options))
        return self._ask


def _ctx():
    ctx = ToolContext()
    ctx.modern_runtime = types.SimpleNamespace(python="C:/fake/python.exe", is_py2=False)
    return ctx


# --------------------------------------------------------------------------- #


def test_multi_step_then_answer(monkeypatch):
    monkeypatch.setattr(tools_mod.ops, "inspect_aprx", lambda *a, **k: _result(json_data={"maps_count": 3}))
    rec = Rec()
    fake = FakeLlm([_tc("inspect_aprx", {"project": "x.aprx"}), _final("完成：3 个地图")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_CONFIRM_WRITES)
    ret = agent.run("检查 x.aprx")
    assert ret == "完成：3 个地图"
    assert rec.text == ["完成：3 个地图"]
    assert len(rec.starts) == 1
    assert rec.starts[0][0] == "inspect_aprx"
    assert rec.confirms == []  # 只读工具不确认


def test_confirm_writes_approves_extract(monkeypatch):
    captured = {}
    def fake_extract(rt, project, out, *, alignment, **k):
        captured["confirmed"] = alignment.confirmed
        return _result(json_data={"output_dir": out})
    monkeypatch.setattr(tools_mod.ops, "extract_data", fake_extract)
    rec = Rec(confirm=True)
    fake = FakeLlm([_tc("extract_data", {"project": "x.aprx", "output_dir": "out_v1"}), _final("已提取")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_CONFIRM_WRITES)
    ret = agent.run("提取 x.aprx")
    assert ret == "已提取"
    assert len(rec.confirms) == 1
    assert rec.confirms[0][0] == "extract_data"
    assert captured["confirmed"] is True


def test_confirm_writes_rejects_extract(monkeypatch):
    called = []
    monkeypatch.setattr(tools_mod.ops, "extract_data", lambda *a, **k: called.append(1) or _result(json_data={}))
    rec = Rec(confirm=False)
    fake = FakeLlm([_tc("extract_data", {"project": "x.aprx", "output_dir": "out_v1"}), _final("已停止")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_CONFIRM_WRITES)
    ret = agent.run("提取 x.aprx")
    assert ret == "已停止"
    assert called == []  # 被拒，未派发
    assert rec.starts == []  # 未到 on_tool_start


def test_autonomous_no_confirm(monkeypatch):
    captured = {}
    def fake_extract(rt, project, out, *, alignment, **k):
        captured["confirmed"] = alignment.confirmed
        return _result(json_data={"output_dir": out})
    monkeypatch.setattr(tools_mod.ops, "extract_data", fake_extract)
    rec = Rec()
    fake = FakeLlm([_tc("extract_data", {"project": "x.aprx", "output_dir": "out_v1"}), _final("ok")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_AUTONOMOUS)
    agent.run("提取")
    assert rec.confirms == []  # 自主模式不确认
    assert captured["confirmed"] is True  # 但对齐块被自动确认


def test_confirm_every_step_pauses_on_read(monkeypatch):
    monkeypatch.setattr(tools_mod.ops, "inspect_aprx", lambda *a, **k: _result(json_data={"maps_count": 1}))
    rec = Rec(confirm=True)
    fake = FakeLlm([_tc("inspect_aprx", {"project": "x.aprx"}), _final("ok")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_CONFIRM_EVERY_STEP)
    agent.run("检查")
    assert len(rec.confirms) == 1  # 只读工具也要确认
    assert rec.confirms[0][0] == "inspect_aprx"


def test_confirm_every_step_rejects_read(monkeypatch):
    called = []
    monkeypatch.setattr(tools_mod.ops, "inspect_aprx", lambda *a, **k: called.append(1) or _result(json_data={}))
    rec = Rec(confirm=False)
    fake = FakeLlm([_tc("inspect_aprx", {"project": "x.aprx"}), _final("ok")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_CONFIRM_EVERY_STEP)
    agent.run("检查")
    assert called == []
    assert rec.starts == []


def test_max_iterations_guard():
    rec = Rec()
    fake = RepeatLlm(_tc("discover_runtimes", {}))
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), max_iterations=3)
    ret = agent.run("无限循环")
    assert "最大迭代数" in ret
    assert len(rec.errors) == 1
    assert fake.calls == 3


def test_unknown_tool_feeds_error():
    rec = Rec()
    fake = FakeLlm([_tc("bogus_tool", {}), _final("ok")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_AUTONOMOUS)
    ret = agent.run("调用不存在的工具")
    assert ret == "ok"
    assert rec.starts == []  # 未知工具不触发 on_tool_start


def test_bad_json_args_feeds_error():
    rec = Rec()
    bad = AssistantMessage(tool_calls=[ToolCall(id="c1", name="inspect_aprx", arguments="not json")])
    fake = FakeLlm([bad, _final("ok")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_AUTONOMOUS)
    ret = agent.run("坏参数")
    assert ret == "ok"
    assert rec.starts == []


def test_invalid_autonomy_raises():
    import pytest
    with pytest.raises(ValueError):
        Agent(FakeLlm([]), _ctx(), autonomy="bogus")


def test_reset_clears_history():
    agent = Agent(FakeLlm([_final("hi")]), _ctx(), autonomy=AUTONOMY_AUTONOMOUS)
    agent.run("第一句")
    assert len(agent.history) == 3  # system + user + assistant
    agent.reset()
    assert len(agent.history) == 1
    assert agent.history[0]["role"] == "system"


# --------------------------------------------------------------------------- #
# ask_user
# --------------------------------------------------------------------------- #


def test_ask_user_answer_continues_loop():
    rec = Rec(ask="方案 B")
    fake = FakeLlm([_tc("ask_user", {"question": "哪个方案？", "options": ["A", "B"]}), _final("继续")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_AUTONOMOUS)
    ret = agent.run("帮我抉择")
    assert ret == "继续"
    assert rec.asks == [("哪个方案？", ["A", "B"])]
    tool_results = [m["content"] for m in agent.history if m["role"] == "tool"]
    assert any("用户回答：方案 B" in r for r in tool_results)


def test_ask_user_no_frontend_feeds_no_answer():
    rec = Rec(ask=None)
    fake = FakeLlm([_tc("ask_user", {"question": "q", "options": ["A"]}), _final("按最佳判断")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_AUTONOMOUS)
    ret = agent.run("抉择")
    assert ret == "按最佳判断"
    tool_results = [m["content"] for m in agent.history if m["role"] == "tool"]
    assert any("用户未回答" in r for r in tool_results)


def test_ask_user_not_gated_by_confirm_every_step(monkeypatch):
    monkeypatch.setattr(tools_mod.ops, "inspect_aprx", lambda *a, **k: _result(json_data={"maps_count": 1}))
    rec = Rec(confirm=True, ask="是")
    fake = FakeLlm([
        _tc("ask_user", {"question": "继续吗？", "options": ["是", "否"]}),
        _tc("inspect_aprx", {"project": "x.aprx"}),
        _final("完成"),
    ])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_CONFIRM_EVERY_STEP)
    ret = agent.run("检查")
    assert ret == "完成"
    assert len(rec.asks) == 1  # ask_user 本身不再套确认
    assert len(rec.confirms) == 1  # 只有 inspect_aprx 被确认
    assert rec.confirms[0][0] == "inspect_aprx"


# --------------------------------------------------------------------------- #
# 流式输出 + 取消
# --------------------------------------------------------------------------- #


def test_streaming_token_flow_and_final_text():
    rec = Rec()
    fake = FakeLlm([_final("你好")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_AUTONOMOUS)
    ret = agent.run("hi")
    assert ret == "你好"
    assert rec.tokens == list("你好")  # 逐 token 回调
    assert rec.text == ["你好"]  # on_assistant_text 仍触发一次完整文本


def test_cancel_mid_stream_raises_and_no_dirty_history():
    cancel = threading.Event()
    rec = Rec()
    fake = FakeLlm([_final("你好")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_AUTONOMOUS,
                  cancel=cancel)

    def _cancel_on_first(token):
        cancel.set()  # 收到第一个 token 即取消
        rec.tokens.append(token)

    agent.callbacks.on_token = _cancel_on_first
    with pytest.raises(LlmCancelled):
        agent.run("hi")
    assert rec.errors == []  # 取消不被 on_error 吞
    # 半截 assistant 消息不入历史：只有 system + user 两条
    roles = [m["role"] for m in agent.history]
    assert roles == ["system", "user"]


def test_cancel_not_swallowed_when_pre_set():
    cancel = threading.Event()
    cancel.set()  # 预置取消
    rec = Rec()
    fake = FakeLlm([_final("hi")])
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_AUTONOMOUS,
                  cancel=cancel)
    with pytest.raises(LlmCancelled):
        agent.run("hi")
    assert rec.errors == []


def test_legacy_fake_without_on_token_still_works():
    # 不接收 on_token 的假实现（如 test_agent_tool_inventory 的 _Llm）走非流式。
    def legacy_chat(messages, tools):
        return AssistantMessage(content="ok")

    agent = Agent(legacy_chat, _ctx(), callbacks=Rec().cb(), autonomy=AUTONOMY_AUTONOMOUS)
    assert agent.run("hi") == "ok"


def test_repeat_llm_with_on_token():
    # RepeatLlm 的 msg 是工具调用无 content，流式回调不应触发。
    rec = Rec()
    fake = RepeatLlm(_tc("inspect_aprx", {"project": "x.aprx"}))
    agent = Agent(fake, _ctx(), callbacks=rec.cb(), autonomy=AUTONOMY_AUTONOMOUS)
    assert agent.run("x") is not None
    assert rec.tokens == []
