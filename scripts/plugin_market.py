#!/usr/bin/env python3
"""Validate the static PCL Nex plugin marketplace registry."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from developer_whitelist import DeveloperWhitelistError, validate_document as validate_developer_document

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "apiv2" / "plugin-market.json"

SCHEMA_VERSION = 1
PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
BASE_VERSION_PATTERN = re.compile(r"^[1-9]\d{3}\.(?:0[1-9]|1[0-2])\.(?:0|[1-9]\d*)$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class PluginMarketError(ValueError):
    """Raised when plugin-market.json does not satisfy its static API contract."""


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PluginMarketError(f"{field} must be a non-empty string")
    return value.strip()


def _absolute_http_url(value: Any, field: str) -> str:
    url = _non_empty_string(value, field)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PluginMarketError(f"{field} must be an absolute HTTP/HTTPS URL")
    if parsed.username or parsed.password:
        raise PluginMarketError(f"{field} must not contain credentials")
    return url


def _utc_timestamp(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise PluginMarketError(f"{field} must be a UTC timestamp or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PluginMarketError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PluginMarketError(f"{field} must use UTC")


def _unique_strings(values: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(values, list):
        raise PluginMarketError(f"{field} must be a JSON array")
    if not allow_empty and not values:
        raise PluginMarketError(f"{field} must not be empty")
    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        item = _non_empty_string(value, f"{field}[{index}]")
        key = item.casefold()
        if key in seen:
            raise PluginMarketError(f"{field} contains a duplicate value: {item}")
        seen.add(key)
        result.append(item)
    return result


def _validate_download(download: Any, field: str) -> None:
    if not isinstance(download, dict):
        raise PluginMarketError(f"{field} must be a JSON object")
    _absolute_http_url(download.get("packageUrl"), f"{field}.packageUrl")
    sha256 = _non_empty_string(download.get("sha256"), f"{field}.sha256")
    if not SHA256_PATTERN.fullmatch(sha256):
        raise PluginMarketError(f"{field}.sha256 must contain 64 hexadecimal characters")


def _validate_inline_plugin(plugin: Any, index: int) -> str:
    field = f"plugins[{index}]"
    if not isinstance(plugin, dict):
        raise PluginMarketError(f"{field} must be a JSON object")
    plugin_id = _non_empty_string(plugin.get("id"), f"{field}.id")
    if not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
        raise PluginMarketError(f"{field}.id is invalid")
    _non_empty_string(plugin.get("name"), f"{field}.name")
    _non_empty_string(plugin.get("description"), f"{field}.description")
    _absolute_http_url(plugin.get("repository"), f"{field}.repository")

    author = plugin.get("author")
    if not isinstance(author, dict):
        raise PluginMarketError(f"{field}.author must be a JSON object")
    if not any(
        isinstance(author.get(key), str) and author[key].strip()
        for key in ("githubLogin", "displayName")
    ):
        raise PluginMarketError(f"{field}.author must declare githubLogin or displayName")

    versions = plugin.get("versions")
    if not isinstance(versions, list) or not versions:
        raise PluginMarketError(f"{field}.versions must be a non-empty JSON array")
    version_names: set[str] = set()
    for version_index, version in enumerate(versions):
        version_field = f"{field}.versions[{version_index}]"
        if not isinstance(version, dict):
            raise PluginMarketError(f"{version_field} must be a JSON object")
        version_name = _non_empty_string(version.get("version"), f"{version_field}.version")
        version_key = version_name.casefold()
        if version_key in version_names:
            raise PluginMarketError(f"{field}.versions contains duplicate version {version_name}")
        version_names.add(version_key)
        core_version = _non_empty_string(version.get("pclCoreVersion"), f"{version_field}.pclCoreVersion")
        if not BASE_VERSION_PATTERN.fullmatch(core_version):
            raise PluginMarketError(f"{version_field}.pclCoreVersion must use yyyy.MM.patch")
        downloads = version.get("downloads")
        if not isinstance(downloads, dict):
            raise PluginMarketError(f"{version_field}.downloads must be a JSON object")
        supported = [key for key in ("amd64", "arm64", "anycpu") if downloads.get(key) is not None]
        if not supported:
            raise PluginMarketError(f"{version_field}.downloads must declare amd64, arm64, or anycpu")
        unknown = set(downloads) - {"amd64", "arm64", "anycpu"}
        if unknown:
            raise PluginMarketError(f"{version_field}.downloads contains unsupported platforms: {sorted(unknown)}")
        for platform in supported:
            _validate_download(downloads[platform], f"{version_field}.downloads.{platform}")
    return plugin_id


def validate_document(document: Any) -> dict[str, Any]:
    """Validate a version 1 developers/manifests/plugins source document."""

    if not isinstance(document, dict):
        raise PluginMarketError("Plugin market document must be a JSON object")
    if document.get("version") != SCHEMA_VERSION:
        raise PluginMarketError(f"version must be {SCHEMA_VERSION}")
    _utc_timestamp(document.get("updatedAt"), "updatedAt")
    for optional_text in ("name", "group"):
        if optional_text in document and document[optional_text] is not None:
            _non_empty_string(document[optional_text], optional_text)

    try:
        validate_developer_document(document)
    except DeveloperWhitelistError as exc:
        raise PluginMarketError(str(exc)) from exc

    _unique_strings(document.get("tags"), "tags")
    if "topics" in document:
        raise PluginMarketError("topics is not supported; GitHub Topic discovery is maintained by the launcher")

    manifests = _unique_strings(document.get("manifests"), "manifests")
    for index, manifest in enumerate(manifests):
        _absolute_http_url(manifest, f"manifests[{index}]")

    plugins = document.get("plugins")
    if not isinstance(plugins, list):
        raise PluginMarketError("plugins must be a JSON array")
    plugin_ids: set[str] = set()
    for index, plugin in enumerate(plugins):
        plugin_id = _validate_inline_plugin(plugin, index)
        key = plugin_id.casefold()
        if key in plugin_ids:
            raise PluginMarketError(f"plugins contains duplicate plugin id: {plugin_id}")
        plugin_ids.add(key)

    return document


def load_document(path: Path = DEFAULT_FILE) -> dict[str, Any]:
    if not path.exists():
        raise PluginMarketError(f"Plugin market data file is missing: {path}")
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PluginMarketError(f"Plugin market data in {path} must be valid UTF-8") from exc
    try:
        document = json.loads(source)
    except json.JSONDecodeError as exc:
        raise PluginMarketError(f"Invalid JSON in {path}: {exc}") from exc
    return validate_document(document)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate apiv2/plugin-market.json.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="Plugin market JSON path.")
    args = parser.parse_args()
    document = load_document(args.file)
    print(
        "Valid plugin market: "
        f"{len(document['developers'])} developer(s), "
        f"{len(document['manifests'])} manifest(s), "
        f"{len(document['plugins'])} inline plugin(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
