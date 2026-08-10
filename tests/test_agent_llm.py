"""LLM 类型与配置测试。"""

from types import SimpleNamespace

from gisdo.agent.llm import (
    THINKING_AUTO,
    THINKING_DISABLED,
    THINKING_HIGH,
    AssistantMessage,
    JsonParseError,
    LlmCancelled,
    LlmClient,
    LlmConfig,
    ToolCall,
    _assistant_from_message,
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


def test_assistant_message_preserves_reasoning_for_tool_followup():
    msg = AssistantMessage(
        reasoning_content="先检查数据源",
        tool_calls=[ToolCall(id="1", name="inspect_aprx", arguments="{}")],
    )
    api = msg.to_api()
    assert api["content"] == ""  # DeepSeek 思考工具调用要求非 null content
    assert api["reasoning_content"] == "先检查数据源"


def test_assistant_from_message_reads_reasoning_content():
    raw = SimpleNamespace(content="完成", reasoning_content="分析过程", tool_calls=[])
    msg = _assistant_from_message(raw)
    assert msg.content == "完成"
    assert msg.reasoning_content == "分析过程"


def test_final_answer_does_not_persist_reasoning_content():
    msg = AssistantMessage(content="完成", reasoning_content="内部分析")
    assert msg.to_api() == {"role": "assistant", "content": "完成"}


def test_llm_config_api_key_from_field():
    assert LlmConfig(api_key="sk-abc").resolved_api_key() == "sk-abc"


def test_llm_config_api_key_from_env(monkeypatch):
    monkeypatch.setenv("GISDO_API_KEY", "sk-env")
    assert LlmConfig(api_key="").resolved_api_key() == "sk-env"
    # 字段优先于环境变量
    assert LlmConfig(api_key="sk-field").resolved_api_key() == "sk-field"


def test_thinking_auto_keeps_provider_default():
    config = LlmConfig(base_url="https://api.deepseek.com", thinking_level=THINKING_AUTO)
    assert config.thinking_extra_body() == {}


def test_thinking_disabled_for_ark_and_deepseek():
    config = LlmConfig(
        base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        model="deepseek-v4-flash",
        thinking_level=THINKING_DISABLED,
    )
    assert config.thinking_extra_body() == {"thinking": {"type": "disabled"}}


def test_thinking_effort_for_ark_and_deepseek():
    config = LlmConfig(
        base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        model="deepseek-v4-flash",
        thinking_level=THINKING_HIGH,
    )
    assert config.thinking_extra_body() == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }


def test_thinking_for_dashscope_uses_enable_thinking():
    config = LlmConfig(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        thinking_level=THINKING_DISABLED,
    )
    assert config.thinking_extra_body() == {"enable_thinking": False}


def test_thinking_disabled_for_standard_endpoint_uses_none():
    config = LlmConfig(
        base_url="https://api.openai.com/v1",
        model="gpt-5.1",
        thinking_level=THINKING_DISABLED,
    )
    assert config.thinking_extra_body() == {"reasoning_effort": "none"}


def test_invalid_thinking_level_falls_back_to_auto():
    config = LlmConfig(thinking_level="unexpected")
    assert config.normalized_thinking_level() == THINKING_AUTO
    assert config.thinking_extra_body() == {}


def test_client_sends_thinking_parameters(monkeypatch):
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content="ok", reasoning_content="想过了", tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    client = LlmClient(LlmConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        model="deepseek-v4-flash",
        thinking_level=THINKING_HIGH,
    ))
    monkeypatch.setattr(client, "_client", lambda: fake_client)
    reply = client.chat([{"role": "user", "content": "hi"}], [])
    assert captured["extra_body"] == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert "tool_choice" not in captured
    assert reply.reasoning_content == "想过了"


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


def test_consume_stream_accumulates_reasoning_without_displaying_it():
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content=None, reasoning_content="先分析", tool_calls=None,
        ))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content="答案", reasoning_content=None, tool_calls=None,
        ))]),
    ]
    tokens = []
    msg = _consume_stream(chunks, tokens.append)
    assert msg.reasoning_content == "先分析"
    assert msg.content == "答案"
    assert tokens == ["答案"]  # 思考过程只回传给模型，不显示到 GUI


def test_llm_cancelled_is_run_cancelled_subclass():
    assert issubclass(LlmCancelled, RunCancelled)
