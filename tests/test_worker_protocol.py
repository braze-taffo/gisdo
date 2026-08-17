from __future__ import print_function

import json
import os
import re
import subprocess
import sys

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def send(process, value):
    process.stdin.write((json.dumps(value) + "\n").encode("utf-8"))
    process.stdin.flush()
    return json.loads(process.stdout.readline().decode("utf-8"))


def test_fake_worker_handshake_and_shutdown():
    environment = dict(os.environ)
    environment["GISDO_WORKER_FAKE_ARCPY"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-u", os.path.join(ROOT, "workers", "pro", "worker_server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
    )
    ready = send(process, {"type": "hello", "protocol": 1})
    assert ready["type"] == "ready"
    assert ready["runtime"] == "pro"
    stopped = send(process, {"type": "shutdown"})
    assert stopped["type"] == "stopped"
    assert process.wait(timeout=5) == 0


def test_worker_discovers_datasets_inside_a_folder(tmp_path):
    source = tmp_path / "广州建筑.shp"
    source.write_bytes(b"fixture")
    environment = dict(os.environ)
    environment["GISDO_WORKER_FAKE_ARCPY"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-u", os.path.join(ROOT, "workers", "pro", "worker_server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
    )
    send(process, {"type": "hello", "protocol": 1})
    inspected = send(process, {
        "type": "inspect_paths", "request_id": "inspect-1",
        "roots": [str(tmp_path)], "max_items": 20,
    })
    assert inspected["type"] == "inspection_completed"
    assert inspected["inventory"]["dataset_count"] == 1
    assert inspected["inventory"]["datasets"][0]["path"].endswith("广州建筑.shp")
    send(process, {"type": "shutdown"})
    assert process.wait(timeout=5) == 0


def test_arcmap_sources_are_python27_syntax_safe():
    # Compile with the current interpreter as a basic floor; the source also
    # deliberately avoids annotations, f-strings, async and pathlib.
    for relative in ("workers/common/worker_core.py", "workers/arcmap/worker_server.py"):
        source = open(os.path.join(ROOT, relative), "rb").read()
        assert re.search(rb"(?<![A-Za-z0-9_.])f[\"']", source) is None
        assert b"async def" not in source
        compile(source, relative, "exec")


def find_python27():
    import glob
    for pattern in (r"C:\Python27\ArcGIS*\python.exe", r"C:\Python27\python.exe"):
        for candidate in sorted(glob.glob(pattern), reverse=True):
            if os.path.isfile(candidate):
                return candidate
    return None


PY2_SNIPPET = r"""
# -*- coding: utf-8 -*-
import json
import os
import sys

sys.path.insert(0, os.path.join({root!r}, "workers", "common"))
os.environ["GISDO_WORKER_FAKE_ARCPY"] = "1"
import worker_core

schema = [
    {{"name": "in_features", "datatype": "GPFeatureLayer", "direction": "Input", "required": True}},
    {{"name": "out_feature_class", "datatype": "DEFeatureClass", "direction": "Output", "required": True}},
    {{"name": "buffer_distance_or_field", "datatype": "Linear Unit", "direction": "Input", "required": True}},
]
worker_core.parameter_schema = lambda arcpy, toolbox, tool: schema
worker_core.validate_input_compatibility = lambda arcpy, path, datatype: None
step = {{
    "id": "buffer", "runtime": "arcmap", "tool": "analysis.Buffer",
    "params": {{"in_features": r"{source}", "out_feature_class": r"{output}", "buffer_distance_or_field": "100 Meters"}},
}}
arcpy = worker_core.load_arcpy()
toolbox, tool_name, params, outputs, has_output = worker_core.validate_official_step(arcpy, step)
assert toolbox == "analysis" and tool_name == "Buffer", (toolbox, tool_name)
assert outputs == [r"{output}"], outputs
assert has_output is True
print("PY2_OK")
"""


@pytest.mark.skipif(find_python27() is None, reason="本机没有 Python 2.7 (ArcGIS Desktop) 解释器")
def test_official_step_validation_runs_on_real_python27(tmp_path):
    # 列表推导式在 Py2 没有独立作用域：此前推导式变量遮蔽外层循环变量，
    # 使任何带数据集参数的官方工具步骤在 ArcMap Worker 上抛 TypeError。
    interpreter = find_python27()
    source = tmp_path / "roads.shp"
    source.write_bytes(b"x" * 1024)
    code = PY2_SNIPPET.format(
        root=ROOT,
        source=str(tmp_path / "roads.shp"),
        output=str(tmp_path / "buffer.shp"),
    )
    completed = subprocess.run(
        [interpreter, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert b"PY2_OK" in completed.stdout


def test_worker_rejects_low_disk_before_write(tmp_path, monkeypatch):
    common = os.path.join(ROOT, "workers", "common")
    sys.path.insert(0, common)
    try:
        import worker_core
        source = tmp_path / "source.shp"
        source.write_bytes(b"x" * 1024)
        monkeypatch.setattr(worker_core, "free_bytes", lambda _path: 0)
        with pytest.raises(worker_core.ProtocolError, match="insufficient free space"):
            worker_core.validate_free_space([str(source)], [str(tmp_path / "output.shp")])
    finally:
        sys.path.remove(common)


def test_worker_rejects_folder_for_feature_parameter(tmp_path):
    common = os.path.join(ROOT, "workers", "common")
    sys.path.insert(0, common)
    try:
        import worker_core
        with pytest.raises(worker_core.ProtocolError, match="requires a dataset"):
            worker_core.validate_input_compatibility(
                worker_core.FakeArcpy(), str(tmp_path), "GPFeatureLayer"
            )
    finally:
        sys.path.remove(common)
