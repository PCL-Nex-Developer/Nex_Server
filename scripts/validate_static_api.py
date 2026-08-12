#!/usr/bin/env python3
"""Validate the files published as Nex_Server's static JSON API."""

from __future__ import annotations

import argparse
from pathlib import Path

from static_api import validate_static_documents, write_cache

ROOT = Path(__file__).resolve().parents[1]
APIV2_DIR = ROOT / "apiv2"
ANNOUNCEMENT_FILE = APIV2_DIR / "announcement.json"
PLUGIN_MARKET_FILE = APIV2_DIR / "plugin-market.json"
PLUGIN_INDEX_FILE = APIV2_DIR / "plugin-index.json"
CACHE_FILE = APIV2_DIR / "cache.json"
UPDATES_DIR = APIV2_DIR / "updates"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Nex_Server static API documents.")
    parser.add_argument(
        "--write-cache",
        action="store_true",
        help="Atomically refresh apiv2/cache.json after validation.",
    )
    args = parser.parse_args()

    plugin_index_file = PLUGIN_INDEX_FILE if PLUGIN_INDEX_FILE.exists() else None
    validate_static_documents(
        plugin_market_file=PLUGIN_MARKET_FILE,
        plugin_index_file=plugin_index_file,
    )
    if args.write_cache:
        cache = write_cache(
            cache_file=CACHE_FILE,
            announcement_file=ANNOUNCEMENT_FILE,
            plugin_market_file=PLUGIN_MARKET_FILE,
            updates_dir=UPDATES_DIR,
            plugin_index_file=plugin_index_file,
        )
        print(f"Valid static API; refreshed {len(cache)} cache entrie(s).")
    else:
        print("Valid static API documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
