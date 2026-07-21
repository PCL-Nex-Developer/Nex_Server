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

    validate_static_documents(plugin_market_file=PLUGIN_MARKET_FILE)
    if args.write_cache:
        cache = write_cache(
            cache_file=CACHE_FILE,
            announcement_file=ANNOUNCEMENT_FILE,
            plugin_market_file=PLUGIN_MARKET_FILE,
            updates_dir=UPDATES_DIR,
        )
        print(f"Valid static API; refreshed {len(cache)} cache entrie(s).")
    else:
        print("Valid static API documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
