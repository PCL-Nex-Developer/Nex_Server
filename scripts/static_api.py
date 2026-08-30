"""Shared validation and cache generation for Nex_Server's static JSON API."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from plugin_market import load_document as load_plugin_market_document


def validate_static_documents(
    *,
    plugin_market_file: Path,
    plugin_index_file: Path | None = None,
) -> None:
    """Fail before publication when the combined public registry is absent or invalid."""

    load_plugin_market_document(plugin_market_file)
    if plugin_index_file is not None:
        load_plugin_market_document(plugin_index_file)
    releases_file = plugin_market_file.parent / "releases.json"
    if releases_file.exists():
        validate_release_document(releases_file)


def validate_release_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid release manifest {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Release manifest must be a schema_version 1 object.")
    channels = document.get("channels")
    if not isinstance(channels, dict) or not channels:
        raise ValueError("Release manifest must contain at least one channel.")
    allowed_targets = {"win-x64", "win-arm64", "linux-x64", "osx-x64", "osx-arm64"}
    for channel_name, channel in channels.items():
        if channel_name not in {"stable", "beta"} or not isinstance(channel, dict):
            raise ValueError(f"Invalid release channel {channel_name!r}.")
        version = channel.get("version")
        if not isinstance(version, str) or channel.get("tag") != f"v{version}":
            raise ValueError(f"Release channel {channel_name!r} has an invalid tag/version pair.")
        assets = channel.get("assets")
        if not isinstance(assets, list) or not assets:
            raise ValueError(f"Release channel {channel_name!r} must contain assets.")
        seen_assets: set[tuple[str, str]] = set()
        has_modelscope_download = False
        for asset in assets:
            if not isinstance(asset, dict):
                raise ValueError(f"Release channel {channel_name!r} contains an invalid asset.")
            target = asset.get("target")
            package_format = asset.get("format")
            if target not in allowed_targets or not isinstance(package_format, str) or not package_format:
                raise ValueError(f"Release channel {channel_name!r} contains an invalid target or format.")
            identity = (target, package_format)
            if identity in seen_assets:
                raise ValueError(f"Release channel {channel_name!r} contains duplicate asset {identity!r}.")
            seen_assets.add(identity)
            file_name = asset.get("file_name")
            if not isinstance(file_name, str) or not file_name or "/" in file_name or "\\" in file_name:
                raise ValueError(f"Release channel {channel_name!r} contains an invalid file name.")
            downloads = asset.get("downloads")
            if not isinstance(downloads, dict) or not isinstance(downloads.get("github"), str):
                raise ValueError(f"Release asset {file_name!r} must contain a GitHub fallback.")
            modelscope_url = downloads.get("modelscope")
            if modelscope_url is not None:
                if (
                    not isinstance(modelscope_url, str)
                    or "/resolve/master/releases/" not in modelscope_url
                    or "auth_key=" in modelscope_url
                ):
                    raise ValueError(f"Release asset {file_name!r} contains an unstable ModelScope URL.")
                has_modelscope_download = True
        if has_modelscope_download and not isinstance(channel.get("modelscope_url"), str):
            raise ValueError(f"Release channel {channel_name!r} is missing its ModelScope directory URL.")
    return document


def md5_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def write_cache(
    *,
    cache_file: Path,
    announcement_file: Path,
    plugin_market_file: Path,
    updates_dir: Path,
    plugin_index_file: Path | None = None,
) -> dict[str, str]:
    """Validate the combined registry and atomically rebuild cache.json from static API bytes."""

    validate_static_documents(
        plugin_market_file=plugin_market_file,
        plugin_index_file=plugin_index_file,
    )
    files = [announcement_file, plugin_market_file]
    releases_file = cache_file.parent / "releases.json"
    if releases_file.exists():
        files.append(releases_file)
    if plugin_index_file is not None:
        files.append(plugin_index_file)
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
