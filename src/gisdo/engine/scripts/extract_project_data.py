#!/usr/bin/env python
"""Copy saved APRX local data stores to a new, verified, versioned directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from inspect_aprx import inspect_project

CONTAINER_SUFFIXES = [".gdb", ".gpkg", ".sqlite"]


def classify_source(source: str) -> dict:
    parsed = urlparse(source)
    if parsed.scheme.lower() in {"http", "https"}:
        return {"source": source, "kind": "service", "root": source, "copyable": False}

    normalized = os.path.normpath(source)
    lowered = normalized.lower()
    for suffix in CONTAINER_SUFFIXES + [".sde"]:
        marker = suffix + os.sep
        index = lowered.find(marker)
        if index < 0 and lowered.endswith(suffix):
            index = len(lowered) - len(suffix)
        if index >= 0:
            root = normalized[: index + len(suffix)]
            if suffix == ".sde":
                return {
                    "source": source,
                    "kind": "enterprise_connection",
                    "root": root,
                    "copyable": False,
                }
            return {
                "source": source,
                "kind": "file_geodatabase" if suffix == ".gdb" else "container_file",
                "root": root,
                "copyable": os.path.exists(root),
            }

    if os.path.isfile(normalized):
        return {"source": source, "kind": "standalone_file", "root": normalized, "copyable": True}
    if os.path.isdir(normalized):
        return {
            "source": source,
            "kind": "standalone_directory",
            "root": normalized,
            "copyable": True,
        }
    return {"source": source, "kind": "missing_or_unsupported", "root": normalized, "copyable": False}


def unique_records(records: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for record in records:
        key = (record["kind"], os.path.normcase(record["root"]))
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def destination_name(source: str, used: set[str]) -> str:
    base = os.path.basename(source.rstrip("\\/")) or "data"
    candidate = base
    if candidate.casefold() in used:
        path = Path(base)
        digest = hashlib.sha1(os.path.normcase(source).encode("utf-8")).hexdigest()[:8]
        candidate = f"{path.stem}_{digest}{path.suffix}"
    used.add(candidate.casefold())
    return candidate


def files_for_standalone(path: Path) -> list[Path]:
    if path.suffix.lower() == ".shp":
        return sorted(
            [candidate for candidate in path.parent.glob(path.stem + ".*") if candidate.is_file()],
            key=lambda item: item.name.casefold(),
        )
    candidates = {path}
    for pattern in (path.name + ".*", path.stem + ".*"):
        candidates.update(candidate for candidate in path.parent.glob(pattern) if candidate.is_file())
    return sorted(candidates, key=lambda item: item.name.casefold())


def tree_manifest(path: Path, include_hashes: bool) -> dict:
    files = sorted([item for item in path.rglob("*") if item.is_file()], key=lambda p: str(p).casefold())
    digest = hashlib.sha256()
    entries = []
    for item in files:
        relative = item.relative_to(path).as_posix()
        size = item.stat().st_size
        file_hash = None
        if include_hashes:
            hasher = hashlib.sha256()
            with item.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
            file_hash = hasher.hexdigest()
        token = f"{relative}|{size}|{file_hash or ''}\n".encode()
        digest.update(token)
        entries.append({"path": relative, "bytes": size, "sha256": file_hash})
    return {
        "file_count": len(files),
        "bytes": sum(entry["bytes"] for entry in entries),
        "content_signature": digest.hexdigest(),
        "entries": entries,
    }


def selected_files_manifest(files: list[Path], include_hashes: bool) -> dict:
    digest = hashlib.sha256()
    entries = []
    for item in sorted(files, key=lambda path: path.name.casefold()):
        size = item.stat().st_size
        file_hash = None
        if include_hashes:
            hasher = hashlib.sha256()
            with item.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
            file_hash = hasher.hexdigest()
        digest.update(f"{item.name}|{size}|{file_hash or ''}\n".encode())
        entries.append({"path": item.name, "bytes": size, "sha256": file_hash})
    return {
        "file_count": len(entries),
        "bytes": sum(entry["bytes"] for entry in entries),
        "content_signature": digest.hexdigest(),
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("output_dir", help="New output directory; must not exist.")
    parser.add_argument("--skip-hashes", action="store_true")
    args = parser.parse_args()

    project = os.path.abspath(args.project)
    output = Path(args.output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite or reuse: {output}")

    inventory = inspect_project(project)
    classified = unique_records([classify_source(source) for source in inventory["data_sources"]])

    active_locks = []
    for record in classified:
        if record["kind"] == "file_geodatabase" and os.path.isdir(record["root"]):
            active_locks.extend(str(path) for path in Path(record["root"]).rglob("*.lock"))
    if active_locks:
        raise RuntimeError(
            "Refusing to snapshot active file geodatabase(s). Lock files: " + "; ".join(active_locks)
        )

    output.mkdir(parents=True, exist_ok=False)
    workspace_dir = output / "workspaces"
    file_dir = output / "files"
    copied = []
    uncopied = []
    used_names: set[str] = set()

    for record in classified:
        if not record["copyable"]:
            uncopied.append(record)
            continue

        source = Path(record["root"])
        name = destination_name(str(source), used_names)
        if record["kind"] in {"file_geodatabase", "standalone_directory"}:
            destination = workspace_dir / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, copy_function=shutil.copy2)
            source_manifest = tree_manifest(source, include_hashes=not args.skip_hashes)
            destination_manifest = tree_manifest(destination, include_hashes=not args.skip_hashes)
        elif record["kind"] == "container_file":
            destination = workspace_dir / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            source_manifest = selected_files_manifest([source], include_hashes=not args.skip_hashes)
            destination_manifest = selected_files_manifest(
                [destination], include_hashes=not args.skip_hashes
            )
        else:
            destination = file_dir / Path(name).stem
            destination.mkdir(parents=True, exist_ok=False)
            standalone_files = files_for_standalone(source)
            for item in standalone_files:
                shutil.copy2(item, destination / item.name)
            source_manifest = selected_files_manifest(
                standalone_files, include_hashes=not args.skip_hashes
            )
            destination_manifest = selected_files_manifest(
                [item for item in destination.iterdir() if item.is_file()],
                include_hashes=not args.skip_hashes,
            )

        exact_match = (
            source_manifest.get("file_count") == destination_manifest.get("file_count")
            and source_manifest.get("bytes") == destination_manifest.get("bytes")
            and source_manifest.get("content_signature") == destination_manifest.get("content_signature")
        )
        copied.append(
            {
                **record,
                "destination": str(destination),
                "source_manifest": source_manifest,
                "destination_manifest": destination_manifest,
                "exact_match": exact_match,
            }
        )

    report = {
        "project": project,
        "output_dir": str(output),
        "project_inventory": inventory,
        "copied": copied,
        "uncopied": uncopied,
        "all_verified": bool(copied) and all(item["exact_match"] for item in copied),
    }
    manifest_path = output / "extraction_manifest.json"
    with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["all_verified"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
