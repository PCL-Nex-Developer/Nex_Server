#!/usr/bin/env python3
"""Sync PCL2-Nex GitHub releases into the static update feed.

The PCL client reads this repository as a static update source:

- apiv2/cache.json
- apiv2/updates/updates-srx64.json
- apiv2/updates/updates-srarm64.json
- apiv2/updates/updates-frx64.json
- apiv2/updates/updates-frarm64.json
- static/patch/{oldSha256}_{newSha256}.patch

Full downloads point directly to the upstream GitHub Release assets. Patch files
are generated from the upstream release executables and stored in this repository.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from static_api import validate_static_documents, write_cache as write_static_cache

OWNER = "PCL-Nex-Developer"
SOURCE_REPO = "PCL2-Nex"
SOURCE_API = f"https://api.github.com/repos/{OWNER}/{SOURCE_REPO}"

ROOT = Path(__file__).resolve().parents[1]
UPDATES_DIR = ROOT / "apiv2" / "updates"
PATCH_DIR = ROOT / "static" / "patch"
CACHE_FILE = ROOT / "apiv2" / "cache.json"
ANNOUNCEMENT_FILE = ROOT / "apiv2" / "announcement.json"
PLUGIN_MARKET_FILE = ROOT / "apiv2" / "plugin-market.json"

CHANNELS = {
    "stable": {
        "github_prerelease": False,
        "configuration": "Release",
        "update_prefix": "sr",
        "require_prerelease": False,
    },
    "beta": {
        "github_prerelease": True,
        "configuration": "Beta",
        "update_prefix": "fr",
        "require_prerelease": None,
    },
}

ARCHES = {
    "x64": {
        "asset_arch": "x64",
        "update_arch": "x64",
    },
    "arm64": {
        "asset_arch": "ARM64",
        "update_arch": "arm64",
    },
}

ASSET_PREFIX = "PCL2_Nex"
BASE_VERSION_TAG = re.compile(
    r"^v(?P<year>[1-9]\d{3})\.(?P<month>0[1-9]|1[0-2])\.(?P<patch>0|[1-9]\d*)$"
)


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    browser_download_url: str
    id: int | None = None
    size: int | None = None
    updated_at: str | None = None
    digest: str | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    name: str | None
    prerelease: bool
    published_at: str
    body: str | None
    assets: list[ReleaseAsset]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync PCL2-Nex releases into Nex_Server update files.")
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"), help="GitHub token for API requests.")
    parser.add_argument("--keep-patches", type=int, default=int(os.environ.get("KEEP_PATCHES", "3")), help="How many previous full packages per channel/arch should receive bsdiff patches.")
    parser.add_argument("--max-patch-mb", type=int, default=int(os.environ.get("MAX_PATCH_MB", "80")), help="Do not keep a generated patch larger than this size.")
    parser.add_argument("--no-proxy", action="store_true", help="Bypass configured HTTP(S) proxies for GitHub API and asset downloads.")
    parser.add_argument("--check-only", action="store_true", help="Only query GitHub releases and print the channel/asset mapping.")
    parser.add_argument("--force", action="store_true", help="Regenerate update JSON even when versions are unchanged.")
    args = parser.parse_args()

    if args.no_proxy:
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

    ensure_dirs()

    # Never publish release data alongside a malformed developer or marketplace
    # registry. This also applies to --check-only so scheduled runs fail visibly.
    validate_static_documents(plugin_market_file=PLUGIN_MARKET_FILE)

    releases = fetch_releases(args.token)
    if args.check_only:
        print_release_mapping(releases)
        return 0

    require_bsdiff()
    changed = False

    for channel, channel_info in CHANNELS.items():
        release = select_release(releases, channel_info)
        if release is None:
            print(f"No {channel} release found; keeping existing update files.", file=sys.stderr)
            continue

        for arch, arch_info in ARCHES.items():
            asset = find_asset(release, channel_info["configuration"], arch_info["asset_arch"])
            if asset is None:
                print(f"No asset for {channel}/{arch} in {release.tag_name}; keeping existing update file.", file=sys.stderr)
                continue

            update_file = UPDATES_DIR / f"updates-{channel_info['update_prefix']}{arch_info['update_arch']}.json"
            previous = read_update_asset(update_file)
            base_version = parse_base_version_tag(release.tag_name)
            existing_version = get_nested(previous, "version", "base")
            new_sha256 = asset_sha256(asset)

            if new_sha256 and not args.force and existing_version == base_version and is_update_current(
                previous,
                expected_sha256=new_sha256,
                expected_download=asset.browser_download_url,
            ):
                print(f"{update_file.name}: already at {base_version} ({new_sha256[:12]})")
                continue

            print(f"{update_file.name}: syncing {base_version} from {asset.name}")
            with tempfile.TemporaryDirectory(prefix="pcl-nex-release-") as temp_root:
                temp_dir = Path(temp_root)
                exe_path = temp_dir / asset.name
                download(asset.browser_download_url, exe_path, args.token)
                downloaded_sha256 = sha256_file(exe_path)
                if new_sha256 and downloaded_sha256 != new_sha256:
                    raise RuntimeError(f"Downloaded asset sha256 mismatch for {asset.name}: expected {new_sha256}, got {downloaded_sha256}")
                new_sha256 = downloaded_sha256

                old_release_downloads = collect_old_release_downloads(
                    releases,
                    release,
                    channel_info["github_prerelease"],
                    channel_info["configuration"],
                    arch_info["asset_arch"],
                    args.keep_patches,
                )

                patches = make_patches(
                    channel=channel,
                    arch=arch,
                    new_exe=exe_path,
                    new_sha256=new_sha256,
                    keep_patches=args.keep_patches,
                    max_patch_mb=args.max_patch_mb,
                    token=args.token,
                    previous=previous,
                    old_release_downloads=old_release_downloads,
                )

            update_json = build_update_document(
                asset=asset,
                release=release,
                base_version=base_version,
                patches=patches,
                sha256=new_sha256,
            )
            write_json(update_file, update_json)
            changed = True

    cleanup_orphan_patches()
    write_cache()

    if changed:
        print("Update feed changed.")
    else:
        print("No release changes detected.")
    return 0


def ensure_dirs() -> None:
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    PATCH_DIR.mkdir(parents=True, exist_ok=True)


def build_update_document(
    *,
    asset: ReleaseAsset,
    release: ReleaseInfo,
    base_version: str,
    patches: list[str],
    sha256: str,
) -> dict[str, Any]:
    if parse_base_version_tag(f"v{base_version}") != base_version:
        raise ValueError(f"Invalid BaseVersion {base_version!r}.")
    return {
        "assets": [
            {
                "file_name": asset.name,
                "version": {"base": base_version},
                "upd_time": release.published_at,
                "downloads": [asset.browser_download_url],
                "patches": patches,
                "sha256": sha256,
                "changelog": release.body or "",
            }
        ]
    }


def require_bsdiff() -> None:
    if shutil.which("bsdiff") is not None:
        return
    try:
        import bsdiff4  # noqa: F401
    except ImportError as exc:
        raise SystemExit("bsdiff or the Python package bsdiff4 is required. Install requirements.txt before running this script.") from exc


def api_request(url: str, token: str | None) -> Any:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "Nex_Server update sync")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_releases(token: str | None) -> list[ReleaseInfo]:
    raw_releases: list[dict[str, Any]] = []
    for page in range(1, 4):
        page_releases = api_request(f"{SOURCE_API}/releases?per_page=100&page={page}", token)
        if not page_releases:
            break
        raw_releases.extend(page_releases)
    releases: list[ReleaseInfo] = []
    for item in raw_releases:
        releases.append(
            ReleaseInfo(
                tag_name=item.get("tag_name") or "",
                name=item.get("name"),
                prerelease=bool(item.get("prerelease")),
                published_at=item.get("published_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                body=item.get("body") or "",
                assets=[
                    ReleaseAsset(
                        name=asset.get("name") or "",
                        browser_download_url=asset.get("browser_download_url") or "",
                        id=asset.get("id"),
                        size=asset.get("size"),
                        updated_at=asset.get("updated_at"),
                        digest=asset.get("digest"),
                    )
                    for asset in item.get("assets", [])
                ],
            )
        )
    return releases


def select_release(releases: list[ReleaseInfo], channel_info: dict[str, Any]) -> ReleaseInfo | None:
    for release in releases:
        if try_parse_base_version_tag(release.tag_name) is None:
            continue
        required_prerelease = channel_info.get("require_prerelease")
        if required_prerelease is not None and release.prerelease != required_prerelease:
            continue
        if all(find_asset(release, channel_info["configuration"], arch_info["asset_arch"]) for arch_info in ARCHES.values()):
            return release
    return None


def find_asset(release: ReleaseInfo, configuration: str, arch: str) -> ReleaseAsset | None:
    expected = f"{ASSET_PREFIX}_{configuration}_{arch}.exe".lower()
    for asset in release.assets:
        if asset.name.lower() == expected:
            return asset
    return None


def asset_sha256(asset: ReleaseAsset) -> str | None:
    if not asset.digest:
        return None
    digest = asset.digest.strip()
    if digest.lower().startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    return digest.lower() if re.fullmatch(r"[0-9a-fA-F]{64}", digest) else None


def print_release_mapping(releases: list[ReleaseInfo]) -> None:
    for channel, channel_info in CHANNELS.items():
        release = select_release(releases, channel_info)
        if release is None:
            print(f"{channel}: no release found")
            continue
        print(f"{channel}: {release.tag_name} prerelease={release.prerelease} published_at={release.published_at}")
        for arch, arch_info in ARCHES.items():
            asset = find_asset(release, channel_info["configuration"], arch_info["asset_arch"])
            if asset:
                print(f"  {arch}: {asset.name} -> {asset.browser_download_url}")
            else:
                print(f"  {arch}: missing asset")


def collect_old_release_downloads(
    releases: list[ReleaseInfo],
    current: ReleaseInfo,
    prerelease: bool,
    configuration: str,
    arch: str,
    limit: int,
) -> list[str]:
    downloads: list[str] = []
    for release in releases:
        if len(downloads) >= limit:
            break
        if release.tag_name == current.tag_name or release.prerelease != prerelease:
            continue
        if try_parse_base_version_tag(release.tag_name) is None:
            continue
        asset = find_asset(release, configuration, arch)
        if asset:
            downloads.append(asset.browser_download_url)
    return downloads


def download(url: str, output: Path, token: str | None) -> None:
    last_error: BaseException | None = None
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(url)
            request.add_header("Accept", "application/octet-stream")
            request.add_header("User-Agent", "Nex_Server update sync")
            if token and ("github.com" in url or "githubusercontent.com" in url):
                request.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(request, timeout=300) as response, output.open("wb") as file:
                shutil.copyfileobj(response, file)
            return
        except (http.client.RemoteDisconnected, TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(attempt * 2)
    if last_error:
        raise last_error


def make_patches(
    *,
    channel: str,
    arch: str,
    new_exe: Path,
    new_sha256: str,
    keep_patches: int,
    max_patch_mb: int,
    token: str | None,
    previous: dict[str, Any] | None,
    old_release_downloads: list[str],
) -> list[str]:
    patches: list[str] = []
    previous_downloads = collect_previous_downloads(previous)
    patch_sources = unique(previous_downloads + old_release_downloads)[:keep_patches]
    for old_url in patch_sources:
        with tempfile.TemporaryDirectory(prefix="pcl-nex-old-") as temp_root:
            temp_dir = Path(temp_root)
            old_download = temp_dir / "old.bin"
            old_exe = temp_dir / "old.exe"
            try:
                download(old_url, old_download, token)
                prepare_old_exe(old_download, old_exe)
            except (OSError, urllib.error.URLError, zipfile.BadZipFile, RuntimeError) as exc:
                print(f"Skipping patch source {old_url}: {exc}", file=sys.stderr)
                continue

            old_sha256 = sha256_file(old_exe)
            if old_sha256 == new_sha256:
                continue

            patch_name = f"{old_sha256}_{new_sha256}.patch"
            patch_path = PATCH_DIR / patch_name
            tmp_patch = patch_path.with_suffix(".patch.tmp")
            run_bsdiff(old_exe, new_exe, tmp_patch)
            if tmp_patch.stat().st_size > max_patch_mb * 1024 * 1024:
                print(f"Skipping large patch {patch_name}: {tmp_patch.stat().st_size} bytes", file=sys.stderr)
                tmp_patch.unlink(missing_ok=True)
                continue
            tmp_patch.replace(patch_path)
            patches.append(patch_name)
    return unique(patches)


def collect_previous_downloads(previous: dict[str, Any] | None) -> list[str]:
    downloads: list[str] = []
    if previous:
        downloads.extend(
            str(item)
            for item in previous.get("downloads", [])
            if item
        )
    return unique(downloads)


def prepare_old_exe(downloaded: Path, output: Path) -> None:
    if zipfile.is_zipfile(downloaded):
        extract_first_exe(downloaded, output)
        return
    shutil.copyfile(downloaded, output)


def extract_first_exe(zip_path: Path, output: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir() and entry.filename.lower().endswith(".exe")]
        if not entries:
            raise RuntimeError(f"No .exe entry found in {zip_path}")
        preferred = sorted(entries, key=lambda entry: ("plain craft launcher" not in entry.filename.lower(), entry.filename))[0]
        with archive.open(preferred) as source, output.open("wb") as target:
            shutil.copyfileobj(source, target)


def run_bsdiff(old_exe: Path, new_exe: Path, patch_path: Path) -> None:
    if patch_path.exists():
        patch_path.unlink()
    if shutil.which("bsdiff") is not None:
        subprocess.run(["bsdiff", str(old_exe), str(new_exe), str(patch_path)], check=True)
        return

    import bsdiff4

    patch_path.write_bytes(bsdiff4.diff(old_exe.read_bytes(), new_exe.read_bytes()))


def cleanup_orphan_patches() -> None:
    active_patches: set[str] = set()
    for update_file in UPDATES_DIR.glob("updates-*.json"):
        asset = read_update_asset(update_file)
        if not asset:
            continue
        active_patches.update(str(item) for item in asset.get("patches", []))

    for patch_file in PATCH_DIR.glob("*.patch"):
        if patch_file.name not in active_patches:
            patch_file.unlink()


def write_cache() -> None:
    write_static_cache(
        cache_file=CACHE_FILE,
        announcement_file=ANNOUNCEMENT_FILE,
        plugin_market_file=PLUGIN_MARKET_FILE,
        updates_dir=UPDATES_DIR,
    )


def read_update_asset(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        return None
    first = assets[0]
    return first if isinstance(first, dict) else None


def is_update_current(asset: dict[str, Any] | None, *, expected_sha256: str, expected_download: str) -> bool:
    if not asset:
        return False
    if asset.get("sha256") != expected_sha256:
        return False
    downloads = asset.get("downloads")
    if not isinstance(downloads, list) or not downloads:
        return False
    return expected_download in [str(item) for item in downloads]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def parse_base_version_tag(tag_name: str) -> str:
    match = BASE_VERSION_TAG.fullmatch(tag_name)
    if match is None:
        raise ValueError(
            f"Invalid release tag {tag_name!r}; expected strict vyyyy.MM.patch format."
        )
    return f"{match.group('year')}.{match.group('month')}.{match.group('patch')}"


def try_parse_base_version_tag(tag_name: str) -> str | None:
    try:
        return parse_base_version_tag(tag_name)
    except ValueError:
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_nested(data: dict[str, Any] | None, *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
