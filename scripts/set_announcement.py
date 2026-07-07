#!/usr/bin/env python3
"""Write apiv2/announcement.json and refresh apiv2/cache.json."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APIV2_DIR = ROOT / "apiv2"
UPDATES_DIR = APIV2_DIR / "updates"
ANNOUNCEMENT_FILE = APIV2_DIR / "announcement.json"
CACHE_FILE = APIV2_DIR / "cache.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Update announcement.json for PCL Nex update API.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--btn1-text", default="")
    parser.add_argument("--btn1-command", default="")
    parser.add_argument("--btn1-command-parameter", default="")
    parser.add_argument("--btn2-text", default="")
    parser.add_argument("--btn2-command", default="")
    parser.add_argument("--btn2-command-parameter", default="")
    parser.add_argument("--append", action="store_true", help="Append to current content instead of replacing it.")
    args = parser.parse_args()

    announcement = {
        "title": args.title,
        "detail": args.detail,
        "id": args.id,
        "date": args.date,
        "btn1": build_button(args.btn1_text, args.btn1_command, args.btn1_command_parameter),
        "btn2": build_button(args.btn2_text, args.btn2_command, args.btn2_command_parameter),
    }

    content = []
    if args.append and ANNOUNCEMENT_FILE.exists():
        current = json.loads(ANNOUNCEMENT_FILE.read_text(encoding="utf-8"))
        if isinstance(current.get("content"), list):
            content = current["content"]
    content = [item for item in content if not isinstance(item, dict) or item.get("id") != args.id]
    content.insert(0, announcement)

    write_json(ANNOUNCEMENT_FILE, {"content": content})
    write_cache()
    return 0


def build_button(text: str, command: str, command_parameter: str) -> dict[str, str] | None:
    if not text and not command and not command_parameter:
        return None
    return {
        "text": text,
        "command": command,
        "command_paramter": command_parameter,
    }


def write_cache() -> None:
    cache: dict[str, str] = {}
    if ANNOUNCEMENT_FILE.exists():
        cache[ANNOUNCEMENT_FILE.name] = md5_file(ANNOUNCEMENT_FILE)
    for update_file in sorted(UPDATES_DIR.glob("updates-*.json")):
        cache[update_file.name] = md5_file(update_file)
    write_json(CACHE_FILE, cache)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
