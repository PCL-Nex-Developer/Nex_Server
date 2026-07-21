"""Shared validation and cache generation for Nex_Server's static JSON API."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from plugin_market import load_document as load_plugin_market_document


def validate_static_documents(*, plugin_market_file: Path) -> None:
    """Fail before publication when the combined public registry is absent or invalid."""

    load_plugin_market_document(plugin_market_file)


def md5_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def write_cache(
    *,
    cache_file: Path,
    announcement_file: Path,
    plugin_market_file: Path,
    updates_dir: Path,
) -> dict[str, str]:
    """Validate the combined registry and atomically rebuild cache.json from static API bytes."""

    validate_static_documents(plugin_market_file=plugin_market_file)
    files = [announcement_file, plugin_market_file]
    files.extend(sorted(updates_dir.glob("updates-*.json")))
    cache = {path.name: md5_file(path) for path in files if path.exists()}
    payload = json.dumps(cache, ensure_ascii=False, separators=(",", ":")) + "\n"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=cache_file.parent,
        prefix=f".{cache_file.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, cache_file)
    return cache
