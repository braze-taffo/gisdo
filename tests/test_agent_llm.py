"""LLM 类型与配置测试。"""

from types import SimpleNamespace

from gisdo.agent.llm import (
    AssistantMessage,
    JsonParseError,
    LlmCancelled,
    LlmConfig,
    ToolCall,
    _consume_stream,
)
from gisdo.engine.runner import RunCancelled


def _chunk(content=None, tc=None):
    delta = {"content": content}
    if tc is not None:
        delta["tool_calls"] = [tc]
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**delta))])


def _tool_chunk(index, id_=None, name=None, arguments=None):
    fn = {"name": name, "arguments": arguments} if (name or arguments) else None
    return SimpleNamespace(index=index, id=id_,
                           function=SimpleNamespace(**fn) if fn is not None else None)


def test_tool_call_parsed_args_empty():
    assert ToolCall(id="1", name="x", arguments="").parsed_args() == {}


def test_tool_call_parsed_args_dict():
    assert ToolCall(id="1", name="x", arguments='{"a": 1}').parsed_args() == {"a": 1}


def test_tool_call_parsed_args_nondict_wrapped():
    assert ToolCall(id="1", name="x", arguments="[1, 2]").parsed_args() == {"value": [1, 2]}


def test_tool_call_parsed_args_bad_json():
    try:
        ToolCall(id="1", name="x", arguments="not json").parsed_args()
    except JsonParseError:
        return
    raise AssertionError("应抛 JsonParseError")


def test_tool_call_to_api():
    api = ToolCall(id="c1", name="inspect_aprx", arguments='{"project":"x"}').to_api()
    assert api["id"] == "c1"
    assert api["type"] == "function"
    assert api["function"]["name"] == "inspect_aprx"
    assert api["function"]["arguments"] == '{"project":"x"}'


def test_assistant_message_content_only():
    msg = AssistantMessage(content="hello")
    assert not msg.has_tool_calls
    assert msg.to_api() == {"role": "assistant", "content": "hello"}


def test_assistant_message_tool_calls_only():
    msg = AssistantMessage(content=None, tool_calls=[ToolCall(id="1", name="x", arguments="{}")])
    assert msg.has_tool_calls
    api = msg.to_api()
    assert api["role"] == "assistant"
    assert "content" not in api  # content 为空时不出现
    assert api["tool_calls"][0]["function"]["name"] == "x"


def test_llm_config_api_key_from_field():
    assert LlmConfig(api_key="sk-abc").resolved_api_key() == "sk-abc"


def test_llm_config_api_key_from_env(monkeypatch):
    monkeypatch.setenv("GISDO_API_KEY", "sk-env")
    assert LlmConfig(api_key="").resolved_api_key() == "sk-env"
    # 字段优先于环境变量
    assert LlmConfig(api_key="sk-field").resolved_api_key() == "sk-field"


# --------------------------------------------------------------------------- #
# 流式消费
# --------------------------------------------------------------------------- #


def test_consume_stream_content_deltas():
    chunks = [_chunk(content="你"), _chunk(content="好"), _chunk(content=None)]
    tokens = []
    msg = _consume_stream(chunks, tokens.append)
    assert tokens == ["你", "好"]  # content=None 的 chunk 不回调
    assert msg.content == "你好"
    assert msg.tool_calls == []


def test_consume_stream_tool_calls_fragments():
    chunks = [
        _chunk(tc=_tool_chunk(0, id_="c1", name="inspect_aprx", arguments='{"pro')),
        _chunk(tc=_tool_chunk(0, id_="c1", arguments='ject": "x"}')),
    ]
    msg = _consume_stream(chunks, lambda _t: None)
    assert len(msg.tool_calls) == 1
    tc = msg.tool_calls[0]
    assert tc.id == "c1"
    assert tc.name == "inspect_aprx"
    assert tc.arguments == '{"project": "x"}'  # 增量拼接


def test_consume_stream_multiple_tool_indexes():
    chunks = [
        _chunk(tc=_tool_chunk(1, id_="c2", name="b", arguments="{}")),
        _chunk(tc=_tool_chunk(0, id_="c1", name="a", arguments="{}")),
    ]
    msg = _consume_stream(chunks, lambda _t: None)
    assert [tc.name for tc in msg.tool_calls] == ["a", "b"]  # 按 index 排序


def test_consume_stream_empty_content_is_none():
    chunks = [_chunk(tc=_tool_chunk(0, id_="c1", name="x", arguments="{}"))]
    msg = _consume_stream(chunks, lambda _t: None)
    assert msg.content is None  # to_api 不含 content 字段
    assert msg.has_tool_calls


def test_llm_cancelled_is_run_cancelled_subclass():
    assert issubclass(LlmCancelled, RunCancelled)
