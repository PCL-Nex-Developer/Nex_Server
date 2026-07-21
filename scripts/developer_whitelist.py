#!/usr/bin/env python3
"""Validate and manage the developers section embedded in plugin-market.json."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "apiv2" / "plugin-market.json"
ANNOUNCEMENT_FILE = ROOT / "apiv2" / "announcement.json"
CACHE_FILE = ROOT / "apiv2" / "cache.json"
UPDATES_DIR = ROOT / "apiv2" / "updates"

SCHEMA_VERSION = 1
OFFICIAL_LEVEL = "official"
TRUSTED_LEVEL = "trusted"
ALLOWED_LEVELS = {OFFICIAL_LEVEL, TRUSTED_LEVEL}
LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


class DeveloperWhitelistError(ValueError):
    """Raised when a whitelist document does not satisfy the API contract."""


def empty_document() -> dict[str, Any]:
    """Return a valid empty combined plugin-market document."""

    return {
        "version": SCHEMA_VERSION,
        "updatedAt": None,
        "name": "PCL Nex Plugin Marketplace",
        "group": "Official",
        "tags": [],
        "developers": [],
        "manifests": [],
        "plugins": [],
    }


def identity_key(github_login: str) -> str:
    """Return the case-insensitive identity key for a GitHub login."""

    if not isinstance(github_login, str):
        raise DeveloperWhitelistError("githubLogin must be a string")
    login = github_login.strip()
    if not LOGIN_PATTERN.fullmatch(login) or "--" in login:
        raise DeveloperWhitelistError(f"Invalid GitHub login: {github_login!r}")
    return login.casefold()


def validate_document(document: Any) -> dict[str, Any]:
    """Validate a whitelist document and return it unchanged."""

    if not isinstance(document, dict):
        raise DeveloperWhitelistError("Whitelist document must be a JSON object")
    if document.get("version") != SCHEMA_VERSION:
        raise DeveloperWhitelistError(f"version must be {SCHEMA_VERSION}")

    updated_at = document.get("updatedAt")
    if updated_at is not None:
        if not isinstance(updated_at, str):
            raise DeveloperWhitelistError("updatedAt must be a UTC timestamp or null")
        try:
            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DeveloperWhitelistError("updatedAt must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise DeveloperWhitelistError("updatedAt must use UTC")

    developers = document.get("developers")
    if not isinstance(developers, list):
        raise DeveloperWhitelistError("developers must be a JSON array")

    identities: set[str] = set()
    for index, developer in enumerate(developers):
        if not isinstance(developer, dict):
            raise DeveloperWhitelistError(f"developers[{index}] must be a JSON object")

        login = developer.get("githubLogin")
        key = identity_key(login)
        if key in identities:
            raise DeveloperWhitelistError(f"Duplicate GitHub login ignoring case: {login}")
        identities.add(key)

        display_name = developer.get("displayName")
        if not isinstance(display_name, str) or not display_name.strip():
            raise DeveloperWhitelistError(f"developers[{index}].displayName must be a non-empty string")
        if developer.get("level") not in ALLOWED_LEVELS:
            raise DeveloperWhitelistError(
                f"developers[{index}].level must be one of {sorted(ALLOWED_LEVELS)!r}"
            )

    return document


def load_document(path: Path = DEFAULT_FILE) -> dict[str, Any]:
    """Load the combined registry, returning a valid empty document when missing."""

    if not path.exists():
        return empty_document()
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DeveloperWhitelistError(f"Whitelist data in {path} must be valid UTF-8") from exc
    try:
        document = json.loads(source)
    except json.JSONDecodeError as exc:
        raise DeveloperWhitelistError(f"Invalid JSON in {path}: {exc}") from exc
    return validate_document(document)


def find_developer(document: dict[str, Any], github_login: str) -> dict[str, Any] | None:
    """Find a developer using GitHub's case-insensitive login identity."""

    validate_document(document)
    requested = identity_key(github_login)
    for developer in document["developers"]:
        if identity_key(developer["githubLogin"]) == requested:
            return developer
    return None


def upsert_developer(
    document: dict[str, Any],
    *,
    github_login: str,
    display_name: str,
    level: str = OFFICIAL_LEVEL,
) -> dict[str, Any]:
    """Add or replace an identity, matching an existing login without case sensitivity."""

    result = deepcopy(validate_document(document))
    entry = {
        "githubLogin": github_login.strip(),
        "displayName": display_name.strip(),
        "level": level,
    }
    validate_document({"version": SCHEMA_VERSION, "updatedAt": None, "developers": [entry]})

    requested = identity_key(github_login)
    result["developers"] = [
        developer
        for developer in result["developers"]
        if identity_key(developer["githubLogin"]) != requested
    ]
    result["developers"].append(entry)
    result["developers"].sort(key=lambda developer: identity_key(developer["githubLogin"]))
    result["updatedAt"] = utc_now()
    return validate_document(result)


def remove_developer(document: dict[str, Any], github_login: str) -> tuple[dict[str, Any], bool]:
    """Remove an identity using case-insensitive matching."""

    result = deepcopy(validate_document(document))
    requested = identity_key(github_login)
    retained = [
        developer
        for developer in result["developers"]
        if identity_key(developer["githubLogin"]) != requested
    ]
    removed = len(retained) != len(result["developers"])
    if removed:
        result["developers"] = retained
        result["updatedAt"] = utc_now()
    return validate_document(result), removed


def write_document(path: Path, document: dict[str, Any]) -> None:
    """Atomically write a compact UTF-8 whitelist document."""

    validate_document(document)
    from plugin_market import validate_document as validate_plugin_market_document

    validate_plugin_market_document(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def write_cache() -> None:
    """Validate the combined registry and atomically refresh the complete static cache."""

    # Local import avoids a module cycle: static_api imports this module's
    # validator in order to share the whitelist contract with every publisher.
    from static_api import write_cache as write_static_cache

    write_static_cache(
        cache_file=CACHE_FILE,
        announcement_file=ANNOUNCEMENT_FILE,
        plugin_market_file=DEFAULT_FILE,
        updates_dir=UPDATES_DIR,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage developers in apiv2/plugin-market.json.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="Plugin market JSON path.")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("validate", help="Validate the current document (or the missing-file fallback).")
    actions.add_parser("list", help="Print the current document.")
    actions.add_parser("ensure", help="Create an empty document if the file is missing.")

    add = actions.add_parser("add", help="Add or update an official developer.")
    add.add_argument("github_login")
    add.add_argument("display_name")

    remove = actions.add_parser("remove", help="Remove an official developer.")
    remove.add_argument("github_login")

    args = parser.parse_args()
    document = load_document(args.file)

    if args.action == "validate":
        print(f"Valid market developers: {len(document['developers'])} developer(s)")
        return 0
    if args.action == "list":
        print(json.dumps(document, ensure_ascii=False, indent=2))
        return 0
    if args.action == "ensure":
        if not args.file.exists():
            write_document(args.file, document)
        if args.file.resolve() == DEFAULT_FILE.resolve():
            write_cache()
        return 0
    if args.action == "add":
        document = upsert_developer(
            document,
            github_login=args.github_login,
            display_name=args.display_name,
        )
        write_document(args.file, document)
        if args.file.resolve() == DEFAULT_FILE.resolve():
            write_cache()
        return 0
    if args.action == "remove":
        document, removed = remove_developer(document, args.github_login)
        if not removed:
            parser.error(f"GitHub login is not present: {args.github_login}")
        write_document(args.file, document)
        if args.file.resolve() == DEFAULT_FILE.resolve():
            write_cache()
        return 0
    raise AssertionError(f"Unhandled action: {args.action}")


if __name__ == "__main__":
    raise SystemExit(main())
