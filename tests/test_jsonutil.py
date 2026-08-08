"""jsonutil 尾 JSON 提取测试。"""

from gisdo.engine.jsonutil import JsonParseError, extract_trailing_json


def test_pure_json():
    assert extract_trailing_json('{"a": 1}') == {"a": 1}


def test_trailing_json_after_gp_messages():
    text = (
        "正在开始: PackageProject...\n"
        "参数: in_project=x.aprx\n"
        "完成: PackageProject\n"
        "{\n"
        '  "project": "x.aprx",\n'
        '  "broken_count": 0\n'
        "}\n"
    )
    parsed = extract_trailing_json(text)
    assert parsed == {"project": "x.aprx", "broken_count": 0}


def test_json_with_nested_braces_in_messages():
    # GP 消息里含 { 字符，但独占 { 的行才是 JSON 起点。
    text = 'msg {not json} here\n{\n  "ok": true\n}\n'
    assert extract_trailing_json(text) == {"ok": True}


def test_list_json():
    assert extract_trailing_json("[1, 2, 3]") == [1, 2, 3]


def test_empty_raises():
    try:
        extract_trailing_json("   ")
    except JsonParseError:
        return
    raise AssertionError("应抛 JsonParseError")


def test_no_json_raises():
    try:
        extract_trailing_json("纯文本，无 JSON\n第二行\n")
    except JsonParseError:
        return
    raise AssertionError("应抛 JsonParseError")
