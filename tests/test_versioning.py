"""版本化输出路径测试。"""

import re
from pathlib import Path

from gisdo.engine.versioning import versioned_file, versioned_path


def test_path_format(tmp_path: Path):
    p = versioned_path(tmp_path, "extract")
    assert re.fullmatch(r"extract_v1_\d{8}", p.name)
    assert not p.exists()


def test_path_increments_on_collision(tmp_path: Path):
    first = versioned_path(tmp_path, "extract")
    first.mkdir()
    second = versioned_path(tmp_path, "extract")
    assert second.name.startswith("extract_v2_")
    assert not second.exists()


def test_file_format(tmp_path: Path):
    p = versioned_file(tmp_path, "map", "png")
    assert re.fullmatch(r"map_v1_\d{8}\.png", p.name)


def test_file_suffix_dot_handling(tmp_path: Path):
    p = versioned_file(tmp_path, "map", ".pdf")
    assert p.suffix == ".pdf"
