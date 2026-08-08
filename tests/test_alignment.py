"""对齐门禁测试。"""

from gisdo.engine.alignment import Alignment, build_draft
from gisdo.engine.safety import SafetyError


def test_block_has_all_fields():
    a = Alignment(authoritative_project="x.aprx", output_location="out/")
    block = a.as_block()
    for field in ["权威工程", "数据主目录", "外部依赖", "现有成果", "拟读取内容",
                  "拟新增内容", "不会修改的内容", "输出位置", "分级字段与规则",
                  "输出格式与尺寸", "GeoScene桌面授权"]:
        assert field in block


def test_gate_blocks_before_confirm():
    a = Alignment()
    try:
        a.require_confirmed()
    except SafetyError:
        return
    raise AssertionError("确认前应阻断写操作")


def test_gate_passes_after_confirm():
    a = Alignment()
    a.confirm()
    a.require_confirmed()  # 不抛即通过


def test_build_draft_classifies_external():
    inv = {
        "data_sources": ["C:/proj/data.gdb/layer1", "D:/external/shp.shp"],
        "broken": [],
    }
    a = build_draft(project="C:/proj/x.aprx", inventory=inv, output_location="out/")
    assert "D:/external/shp.shp" in a.external_dependencies
    assert a.authoritative_project == "C:/proj/x.aprx"
