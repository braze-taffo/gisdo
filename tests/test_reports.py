"""类型化报告读取器测试。"""

from gisdo.engine.reports import (
    ExtractionManifest,
    PackageReport,
    RenderReport,
    ValidationReport,
    VerifyReport,
)


def test_extraction_manifest():
    data = {
        "output_dir": "/out",
        "all_verified": True,
        "copied": [{"root": "a.gdb"}, {"root": "b.gdb"}],
        "uncopied": [{"kind": "service"}],
    }
    m = ExtractionManifest.from_json(data)
    assert m.all_verified is True
    assert m.copied_count == 2
    assert m.uncopied_count == 1
    assert m.output_dir == "/out"


def test_package_report():
    data = {"output_ppkx": "/x.ppkx", "output_bytes": 100, "maps_count": 2, "broken_count": 0, "output_exists": True}
    r = PackageReport.from_json(data)
    assert r.exists is True
    assert r.maps_count == 2
    assert r.output_bytes == 100


def test_validation_report():
    data = {"passed": True, "extracted_aprx": "/a.aprx", "broken_count": 0, "layout_signatures_match": True, "extracted_file_count": 5}
    r = ValidationReport.from_json(data)
    assert r.passed is True
    assert r.layout_signatures_match is True
    assert r.extracted_file_count == 5


def test_verify_report():
    data = {"passed": True, "width": 400, "height": 300, "non_white_pixels": 7433, "non_white_ratio": 0.062}
    r = VerifyReport.from_json(data)
    assert r.passed is True
    assert r.width == 400
    assert abs(r.non_white_ratio - 0.062) < 1e-9


def test_render_report():
    data = {"output_png": "/m.png", "passed": True, "feature_count": 10, "breaks": [0, 20, 40], "value_min": 1.0, "value_max": 99.0}
    r = RenderReport.from_json(data)
    assert r.passed is True
    assert r.feature_count == 10
    assert r.breaks == [0.0, 20.0, 40.0]


def test_handles_missing_fields():
    r = ExtractionManifest.from_json({})
    assert r.all_verified is False
    assert r.copied_count == 0
