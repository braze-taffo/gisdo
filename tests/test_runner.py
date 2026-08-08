"""runner 集成冒烟测试：用应用 Python 跑 discover_geoscene.py --list-only。

需要 gisdo 已安装（editable 即可）。不依赖 arcpy。
"""

import sys

from gisdo.engine.runner import run_script


def test_run_script_parses_json():
    result = run_script(sys.executable, "discover_geoscene.py", ["--list-only"])
    assert result.script == "discover_geoscene.py"
    # --list-only 返回 0（有候选）或 2（无候选）；两种情况都应解析出 JSON。
    assert result.json is not None
    assert "modern_candidates" in result.json
    assert "legacy_arcmap_candidates" in result.json


def test_run_script_streams_stdout():
    lines = []
    result = run_script(
        sys.executable, "discover_geoscene.py", ["--list-only"],
        on_stdout=lambda l: lines.append(l),
    )
    assert lines  # 流式回调收到了行
    assert result.json is not None
