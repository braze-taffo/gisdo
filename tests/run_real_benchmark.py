from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid


def send(process: subprocess.Popen[bytes], value: dict) -> None:
    assert process.stdin is not None
    process.stdin.write((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    process.stdin.flush()


def receive(process: subprocess.Popen[bytes]) -> dict:
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("worker exited before returning an event")
    return json.loads(line.decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the GISdo Project -> Clip real benchmark")
    parser.add_argument("--python", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()

    for source in (args.python, args.source, args.boundary):
        if not os.path.exists(source):
            raise SystemExit("input does not exist: %s" % source)
    if os.path.exists(args.output_root):
        raise SystemExit("output root already exists: %s" % args.output_root)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    worker = os.path.join(root, "workers", "pro", "worker_server.py")
    projected = os.path.join(args.output_root, "Guangzhou_Buildings_DWG_WGS84_v1_20260811.shp")
    clipped = os.path.join(args.output_root, "Conghua_Buildings_Clip_v1_20260811.shp")
    steps = [
        {
            "id": "project_buildings", "runtime": "pro", "tool": "management.Project",
            "params": {"in_dataset": args.source, "out_dataset": projected, "out_coor_system": "EPSG:4326"},
            "depends_on": [], "validation": "dataset",
        },
        {
            "id": "clip_conghua", "runtime": "pro", "tool": "analysis.Clip",
            "params": {"in_features": projected, "clip_features": args.boundary, "out_feature_class": clipped},
            "depends_on": ["project_buildings"], "validation": "dataset",
        },
    ]
    canonical = json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request_id = str(uuid.uuid4())
    process = subprocess.Popen(
        [args.python, "-u", worker], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    cold_started = time.perf_counter()
    send(process, {"type": "hello", "protocol": 1})
    ready = receive(process)
    ready_ms = round((time.perf_counter() - cold_started) * 1000)
    if ready.get("type") != "ready":
        raise RuntimeError("worker handshake failed: %r" % ready)

    execution_started = time.perf_counter()
    send(process, {
        "type": "execute_plan", "request_id": request_id, "task_id": str(uuid.uuid4()),
        "plan_hash": hashlib.sha256(canonical).hexdigest(), "steps": steps,
    })
    events = []
    failed = None
    while True:
        event = receive(process)
        events.append(event)
        if event.get("type") == "error":
            failed = event
            break
        if event.get("type") == "plan_completed":
            break
    execution_ms = round((time.perf_counter() - execution_started) * 1000)
    stopped = {"type": "exited"}
    if process.poll() is None:
        try:
            send(process, {"type": "shutdown"})
            stopped = receive(process)
        except (BrokenPipeError, RuntimeError) as error:
            stopped = {"type": "shutdown_error", "message": str(error)}
    process.wait(timeout=10)
    stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
    summary = {
        "ok": failed is None and stopped.get("type") == "stopped",
        "ready_ms": ready_ms,
        "execution_ms": execution_ms,
        "cold_total_ms": round((time.perf_counter() - cold_started) * 1000),
        "runtime": ready,
        "outputs": [projected, clipped],
        "events": events,
        "stderr_tail": stderr[-4000:],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    report_bytes = json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        if os.path.isdir(args.output_root):
            with open(os.path.join(args.output_root, "benchmark_report.json"), "wb") as handle:
                handle.write(report_bytes)
    except OSError:
        fallback = os.path.join(root, "target", "benchmark-results")
        if not os.path.isdir(fallback):
            os.makedirs(fallback)
        with open(os.path.join(fallback, os.path.basename(args.output_root) + ".json"), "wb") as handle:
            handle.write(report_bytes)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
