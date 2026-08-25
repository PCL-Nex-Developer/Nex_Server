#!/usr/bin/env python3
"""Auto-index the pclnexplugin GitHub topic into apiv2/plugin-index.json.

Every PCL2-Nex client currently discovers plugins by calling the GitHub
Search/Contents/Releases APIs at launch. That costs one search request plus
several per-plugin requests on every store open, which burns the shared API
rate limit. This script instead materializes the full plugin registry into the
static apiv2/plugin-index.json file so clients can read one JSON document
instead of hammering api.github.com.

The generated pre-index is written to its own file (apiv2/plugin-index.json)
and leaves the curated apiv2/plugin-market.json untouched, which is reserved
as an additional data source. The official developer whitelist is inherited
from plugin-market.json so official developers keep their trust level. The
document inlines the validated manifest of every repository that carries the
pclnexplugin topic. Manifests that do not satisfy the PCL2-Nex market contract
(repository must match the discovered repository, release URLs must match,
downloads must carry a valid SHA-256, ...) are skipped and reported.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from developer_whitelist import utc_now, validate_document as validate_developer_document
from plugin_market import BASE_VERSION_PATTERN, SHA256_PATTERN, PluginMarketError, iter_download_entries
from plugin_market import validate_document as validate_plugin_market_document

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "apiv2" / "plugin-index.json"
OFFICIAL_FILE = ROOT / "apiv2" / "plugin-market.json"

TOPIC = "pclnexplugin"
SEARCH_URL = f"https://api.github.com/search/repositories?q=topic%3A{TOPIC}&sort=updated&order=desc"
MANIFEST_NAME = "manifest.json"
MAX_PAGES = 10
PER_PAGE = 100

PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}(?:-[0-9A-Za-z.-]+)?$")


class IndexError(ValueError):
    """Raised when a topic repository cannot be indexed."""


def api_request(url: str, token: str | None) -> Any:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "Nex_Server plugin market index")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_manifest(owner: str, repo: str, token: str | None) -> dict[str, Any]:
    """Fetch manifest.json using the contents API so the default branch is resolved."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{MANIFEST_NAME}"
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github.raw+json")
    request.add_header("User-Agent", "Nex_Server plugin market index")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise IndexError("manifest.json not found in repository root") from exc
        raise


def fetch_readme_url(owner: str, repo: str, token: str | None) -> str | None:
    try:
        result = api_request(f"https://api.github.com/repos/{owner}/{repo}/readme", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    value = result.get("download_url") if isinstance(result, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def fetch_manifest_updated_at(owner: str, repo: str, token: str | None) -> str | None:
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/commits"
        f"?path={urllib.parse.quote(MANIFEST_NAME)}&per_page=1"
    )
    result = api_request(url, token)
    if not isinstance(result, list) or not result:
        return None
    commit = result[0].get("commit") or {}
    identity = commit.get("committer") or commit.get("author") or {}
    value = identity.get("date")
    return value.strip() if isinstance(value, str) and value.strip() else None


def fetch_release_download_count(owner: str, repo: str, token: str | None) -> int:
    total = 0
    for page in range(1, 11):
        releases = api_request(
            f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100&page={page}",
            token,
        )
        if not isinstance(releases, list):
            raise IndexError("GitHub releases response must be a JSON array")
        for release in releases:
            for asset in release.get("assets") or []:
                count = asset.get("download_count")
                if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                    total += count
        if len(releases) < 100:
            break
    return total


def build_index_metadata(
    repository: dict[str, Any],
    owner: str,
    repo: str,
    default_branch: str,
    token: str | None,
    previous: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None, list[str]]:
    previous_index = (previous or {}).get("index") or {}
    warnings: list[str] = []

    def fetch_or_previous(label: str, fetcher, fallback):
        try:
            return fetcher()
        except Exception as exc:  # noqa: BLE001 - stale metadata is better than dropping the plugin
            warnings.append(f"{label}: {exc}")
            return fallback

    readme_url = fetch_or_previous(
        "README metadata",
        lambda: fetch_readme_url(owner, repo, token),
        (previous or {}).get("readmeUrl"),
    )
    updated_at = fetch_or_previous(
        "manifest commit time",
        lambda: fetch_manifest_updated_at(owner, repo, token),
        previous_index.get("lastUpdatedAt"),
    )
    download_count = fetch_or_previous(
        "release download count",
        lambda: fetch_release_download_count(owner, repo, token),
        previous_index.get("downloadCount", 0),
    )
    metadata = {
        "manifestUrl": (
            f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/{MANIFEST_NAME}"
        ),
        "lastUpdatedAt": updated_at,
        "downloadCount": max(0, int(download_count or 0)),
        "archived": bool(repository.get("archived")),
        "disabled": bool(repository.get("disabled")),
        "fork": bool(repository.get("fork")),
    }
    return metadata, readme_url, warnings


def search_topic(token: str | None) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        result = api_request(f"{SEARCH_URL}&per_page={PER_PAGE}&page={page}", token)
        items = result.get("items") or []
        repositories.extend(items)
        if len(items) < PER_PAGE:
            break
        if page == MAX_PAGES:
            print(f"Reached page limit {MAX_PAGES}; remaining repositories ignored.", file=sys.stderr)
    return repositories


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IndexError(f"{field} must be a non-empty string")
    return value.strip()


def _absolute_http_url(value: Any, field: str) -> str:
    url = _non_empty_string(value, field)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise IndexError(f"{field} must be an absolute HTTP/HTTPS URL")
    return url


def _validate_dependency(value: Any, field: str) -> None:
    if not isinstance(value, dict):
        raise IndexError(f"{field} must be a JSON object")
    if not isinstance(value.get("id"), str) or not value["id"].strip():
        raise IndexError(f"{field}.id must be a non-empty string")
    if "version" in value and not isinstance(value["version"], str):
        raise IndexError(f"{field}.version must be a string")


def _validate_release_links(version: dict[str, Any], owner: str, repo: str) -> None:
    tag = _non_empty_string(version.get("version"), "versions[].version")
    release_notes = version.get("releaseNotes")
    if release_notes is not None:
        release_notes = _non_empty_string(release_notes, "versions[].releaseNotes")
        if not _is_release_tag_url(release_notes, owner, repo, tag):
            raise IndexError(
                f"versions[].releaseNotes must be https://github.com/{owner}/{repo}/releases/tag/{tag}"
            )
    for download_field, download in iter_download_entries(
        version.get("downloads"), "downloads", IndexError
    ):
        package_url = _non_empty_string(download.get("packageUrl"), f"{download_field}.packageUrl")
        if not _is_release_download_url(package_url, owner, repo, tag):
            raise IndexError(
                f"{download_field}.packageUrl must be "
                f"https://github.com/{owner}/{repo}/releases/download/{{tag}}/*.pclx"
            )


def _is_release_tag_url(url: str, owner: str, repo: str, version: str) -> bool:
    path = urllib.parse.urlsplit(url).path
    for tag in (version, f"v{version}"):
        if path == f"/{owner}/{repo}/releases/tag/{tag}":
            return True
    return False


def _is_release_download_url(url: str, owner: str, repo: str, version: str) -> bool:
    path = urllib.parse.urlsplit(url).path
    for tag in (version, f"v{version}"):
        prefix = f"/{owner}/{repo}/releases/download/{tag}/"
        if path.startswith(prefix) and path.endswith(".pclx"):
            return True
    return False


def validate_manifest(
    manifest: dict[str, Any],
    *,
    owner: str,
    repo: str,
    default_branch: str,
) -> dict[str, Any]:
    """Validate a topic manifest against the PCL2-Nex market contract and canonicalize URLs."""
    if not isinstance(manifest, dict):
        raise IndexError("manifest.json must be a JSON object")

    plugin_id = _non_empty_string(manifest.get("id"), "id")
    if not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
        raise IndexError("id is invalid")
    name = _non_empty_string(manifest.get("name"), "name")
    description = _non_empty_string(manifest.get("description"), "description")

    author = manifest.get("author")
    if not isinstance(author, dict):
        raise IndexError("author must be a JSON object")
    github_login = author.get("githubLogin")
    if github_login is not None:
        github_login = _non_empty_string(github_login, "author.githubLogin")
        if not LOGIN_PATTERN.fullmatch(github_login) or "--" in github_login:
            raise IndexError("author.githubLogin is invalid")
        if not github_login.casefold() == owner.casefold():
            raise IndexError("author.githubLogin must match the repository owner")
    display_name = author.get("displayName")
    if not isinstance(display_name, str) or not display_name.strip():
        raise IndexError("author must declare githubLogin or displayName")

    repository_url = _absolute_http_url(manifest.get("repository"), "repository")
    repository_path = urllib.parse.urlsplit(repository_url).path.strip("/")
    if not repository_path.casefold() == f"{owner}/{repo}".casefold():
        raise IndexError(
            f"repository must be https://github.com/{owner}/{repo} "
            f"to match the discovered repository"
        )

    versions = manifest.get("versions")
    if not isinstance(versions, list) or not versions:
        raise IndexError("versions must be a non-empty JSON array")
    version_names: set[str] = set()
    for index, version in enumerate(versions):
        version_field = f"versions[{index}]"
        if not isinstance(version, dict):
            raise IndexError(f"{version_field} must be a JSON object")
        version_name = _non_empty_string(version.get("version"), f"{version_field}.version")
        if not VERSION_PATTERN.fullmatch(version_name):
            raise IndexError(f"{version_field}.version is invalid")
        if version_name.casefold() in version_names:
            raise IndexError(f"versions contains duplicate version {version_name}")
        version_names.add(version_name.casefold())
        core_version = _non_empty_string(version.get("pclCoreVersion"), f"{version_field}.pclCoreVersion")
        if not BASE_VERSION_PATTERN.fullmatch(core_version):
            raise IndexError(f"{version_field}.pclCoreVersion must use yyyy.MM.patch")
        for download_field, download in iter_download_entries(
            version.get("downloads"), f"{version_field}.downloads", IndexError
        ):
            if not isinstance(download, dict):
                raise IndexError(f"{download_field} must be a JSON object")
            package_url = _absolute_http_url(download.get("packageUrl"), f"{download_field}.packageUrl")
            if not urllib.parse.urlsplit(package_url).path.lower().endswith(".pclx"):
                raise IndexError(f"{download_field}.packageUrl must point to a .pclx package")
            sha256 = _non_empty_string(download.get("sha256"), f"{download_field}.sha256")
            if not SHA256_PATTERN.fullmatch(sha256):
                raise IndexError(f"{download_field}.sha256 must contain 64 hexadecimal characters")
        _validate_release_links(version, owner, repo)

    for index, dependency in enumerate(manifest.get("dependencies") or []):
        _validate_dependency(dependency, f"dependencies[{index}]")

    canonical = {
        "id": plugin_id,
        "name": name,
        "author": {
            "githubLogin": author.get("githubLogin") or owner,
            "displayName": display_name.strip(),
        },
        "description": description,
        "readmeUrl": _canonicalize_relative(manifest.get("readmeUrl"), owner, repo, default_branch),
        "repository": f"https://github.com/{owner}/{repo}",
        "homepageUrl": _canonicalize_relative(manifest.get("homepageUrl"), owner, repo, default_branch)
        or f"https://github.com/{owner}/{repo}",
        "group": manifest.get("group"),
        "tags": manifest.get("tags") or [],
        "dependencies": manifest.get("dependencies") or [],
        "versions": versions,
    }
    logo = _canonicalize_relative(manifest.get("logo"), owner, repo, default_branch)
    if logo:
        canonical["logo"] = logo
    return {key: value for key, value in canonical.items() if value is not None}


def _canonicalize_relative(value: Any, owner: str, repo: str, default_branch: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if re.match(r"^https?://", value, re.IGNORECASE):
        return value
    path = value.lstrip("/")
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/{path}"


def build_document(
    *,
    token: str | None,
    existing: dict[str, Any] | None,
    developers: list[dict[str, Any]],
    max_repos: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Discover the topic, validate every manifest and rebuild the pre-index document."""
    repositories = search_topic(token)
    print(f"Discovered {len(repositories)} repository/ies for topic {TOPIC}.")

    plugins: list[dict[str, Any]] = []
    skipped: list[str] = []
    previous_plugins = {
        str(plugin.get("repository", "")).casefold(): plugin
        for plugin in (existing or {}).get("plugins", [])
        if isinstance(plugin, dict) and plugin.get("repository")
    }
    for repository in repositories:
        full_name = repository.get("full_name") or "?"
        if max_repos is not None and len(plugins) >= max_repos:
            break
        if repository.get("archived"):
            skipped.append(f"{full_name}: archived")
            continue
        if repository.get("disabled"):
            skipped.append(f"{full_name}: disabled")
            continue
        if repository.get("fork"):
            skipped.append(f"{full_name}: fork")
            continue
        owner = (repository.get("owner") or {}).get("login")
        repo = repository.get("name")
        default_branch = repository.get("default_branch") or "main"
        if not owner or not repo:
            skipped.append(f"{full_name}: missing owner/name")
            continue
        try:
            manifest = fetch_manifest(owner, repo, token)
            plugin = validate_manifest(
                manifest, owner=owner, repo=repo, default_branch=default_branch
            )
            previous = previous_plugins.get(f"https://github.com/{owner}/{repo}".casefold())
            metadata, readme_url, metadata_warnings = build_index_metadata(
                repository, owner, repo, default_branch, token, previous
            )
            plugin["index"] = metadata
            if not plugin.get("readme") and not plugin.get("readmeUrl") and readme_url:
                plugin["readmeUrl"] = readme_url
            if not plugin.get("logo"):
                avatar_url = (repository.get("owner") or {}).get("avatar_url")
                if isinstance(avatar_url, str) and avatar_url.strip():
                    plugin["logo"] = avatar_url.strip()
            repository_topics = repository.get("topics") or []
            plugin["tags"] = list(dict.fromkeys(
                tag.strip()
                for tag in [*(plugin.get("tags") or []), *repository_topics]
                if isinstance(tag, str) and tag.strip() and tag.strip().casefold() != TOPIC
            ))
            for warning in metadata_warnings:
                skipped.append(f"{full_name}: kept cached {warning}")
            plugins.append(plugin)
        except Exception as exc:  # noqa: BLE001 - indexer must survive broken plugins
            previous = previous_plugins.get(f"https://github.com/{owner}/{repo}".casefold())
            if previous is not None:
                plugins.append(previous)
                skipped.append(f"{full_name}: kept previous index entry after refresh failure: {exc}")
            else:
                skipped.append(f"{full_name}: {exc}")

    plugins.sort(key=lambda plugin: plugin["id"].casefold())
    unique: dict[str, dict[str, Any]] = {}
    for plugin in plugins:
        unique.setdefault(plugin["id"].casefold(), plugin)
    plugins = list(unique.values())

    document = {
        "version": 1,
        "updatedAt": None,
        "name": "PCL Nex Plugin Pre-Index",
        "group": "Official",
        "tags": ["official"],
        "developers": developers,
        "manifests": [],
        "plugins": plugins,
    }
    validate_developer_document(document)
    validate_plugin_market_document(document)
    return document, skipped


def _existing_document(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _serialize(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def write_document(path: Path, document: dict[str, Any]) -> None:
    validate_plugin_market_document(document)
    payload = _serialize(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-index the pclnexplugin topic into plugin-index.json.")
    parser.add_argument(
        "--token",
        default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"),
        help="GitHub token for API requests.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help="Plugin market pre-index JSON path.",
    )
    parser.add_argument(
        "--max-repos",
        type=int,
        default=None,
        help="Limit the number of repositories indexed (default: all discovered).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the document and print the summary without writing files.",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Bypass configured HTTP(S) proxies for GitHub API requests.",
    )
    args = parser.parse_args()

    if args.no_proxy:
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

    official = _existing_document(OFFICIAL_FILE) or {}
    developers = list(official.get("developers") or [])
    existing = _existing_document(args.file)
    document, skipped = build_document(
        token=args.token,
        existing=existing,
        developers=developers,
        max_repos=args.max_repos,
    )

    for entry in skipped:
        print(f"  skipped: {entry}", file=sys.stderr)

    if args.dry_run:
        print(
            f"Would write {len(document['plugins'])} inline plugin(s) "
            f"to {args.file} (skipped {len(skipped)})."
        )
        return 0

    unchanged = (
        existing is not None
        and document["plugins"] == existing.get("plugins", [])
        and document["developers"] == existing.get("developers", [])
    )
    if unchanged:
        print("Plugin market pre-index unchanged; no write required.")
        return 0

    document["updatedAt"] = utc_now()
    write_document(args.file, document)
    print(
        f"Wrote {len(document['plugins'])} inline plugin(s) to {args.file} "
        f"(skipped {len(skipped)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
