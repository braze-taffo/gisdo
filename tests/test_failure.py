"""失败处理测试。"""

from datetime import date
from pathlib import Path
from unittest.mock import patch

from gisdo.engine.failure import (
    BLANK_EXPORT,
    BROKEN_SOURCE,
    LOCK,
    OVERWRITE,
    UNKNOWN,
    VALIDATION,
    FailureRecord,
    categorize,
    detect_partial_outputs,
    propose_retry_path,
)
from gisdo.engine.runner import ScriptResult

_FIXED_VERSION_DATE = date(2026, 8, 8)


def _result(returncode=1, stderr="", stdout="", json_data=None, script="x.py"):
    return ScriptResult(
        script=script,
        interpreter="python",
        args=["a"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_s=0.1,
        json=json_data,
        json_error=None,
    )


def test_categorize_lock():
    assert categorize(_result(stderr="ERROR 000464: schema lock")) == LOCK


def test_categorize_broken_source():
    assert categorize(_result(stderr="broken data source")) == BROKEN_SOURCE


def test_categorize_blank_export():
    assert categorize(_result(returncode=3, stdout="non_white_ratio 0.0")) == BLANK_EXPORT


def test_categorize_overwrite():
    assert categorize(_result(stderr="Refusing to overwrite: x")) == OVERWRITE


def test_categorize_validation_rc3():
    assert categorize(_result(returncode=3, stderr="hash mismatch")) == VALIDATION


def test_categorize_unknown():
    assert categorize(_result(stderr="something else")) == UNKNOWN


def test_detect_partial_outputs_lists_dir(tmp_path: Path):
    out = tmp_path / "extract_v1_20260808"
    out.mkdir()
    (out / "a.bin").write_bytes(b"x")
    (out / "b.bin").write_bytes(b"y")
    partials = detect_partial_outputs(str(out))
    assert len(partials) == 2
    assert all("extract_v1" in p for p in partials)


def test_detect_partial_outputs_missing_returns_empty(tmp_path: Path):
    assert detect_partial_outputs(str(tmp_path / "nope")) == []


def test_detect_partial_outputs_file(tmp_path: Path):
    f = tmp_path / "map.png"
    f.write_bytes(b"x")
    assert detect_partial_outputs(str(f)) == [str(f)]


@patch("gisdo.engine.versioning.date")
def test_propose_retry_path_bumps_version(mock_date, tmp_path: Path):
    mock_date.today.return_value = _FIXED_VERSION_DATE
    current = str(tmp_path / "extract_v1_20260808")
    retry = propose_retry_path(current)
    assert retry is not None
    assert "extract_v2_20260808" in retry


@patch("gisdo.engine.versioning.date")
def test_propose_retry_path_file(mock_date, tmp_path: Path):
    mock_date.today.return_value = _FIXED_VERSION_DATE
    current = str(tmp_path / "map_v1_20260808.png")
    retry = propose_retry_path(current)
    assert retry is not None
    assert retry.endswith("map_v2_20260808.png")


def test_propose_retry_path_non_versioned_returns_none(tmp_path: Path):
    assert propose_retry_path(str(tmp_path / "plain_output")) is None
    assert propose_retry_path(None) is None


@patch("gisdo.engine.versioning.date")
def test_failure_record_from_result_and_format(mock_date, tmp_path: Path):
    mock_date.today.return_value = _FIXED_VERSION_DATE
    out = str(tmp_path / "extract_v1_20260808")
    Path(out).mkdir()
    (Path(out) / "partial.bin").write_bytes(b"x")
    result = _result(returncode=1, stderr="schema lock held", json_data={"messages": "GP msg"})
    rec = FailureRecord.from_result(result, output_path=out)
    assert rec.category == LOCK
    assert rec.messages == "GP msg"
    assert any("partial.bin" in p for p in rec.partial_outputs)
    report = rec.format_report()
    assert "失败脚本" in report
    assert "schema lock" in report
    assert "建议重试路径" in report
    assert "extract_v2_20260808" in report
    assert "未清理" in report
