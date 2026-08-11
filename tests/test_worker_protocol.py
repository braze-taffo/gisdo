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
