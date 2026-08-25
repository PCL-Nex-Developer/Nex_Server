from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from index_plugin_market import IndexError, build_document, validate_manifest  # noqa: E402
from plugin_market import PluginMarketError, validate_document  # noqa: E402


SHA256 = "a" * 64


def download(name: str) -> dict[str, str]:
    return {
        "packageUrl": f"https://github.com/example/plugin/releases/download/v1.0.0/{name}.pclx",
        "sha256": SHA256,
    }


def manifest(downloads: dict) -> dict:
    return {
        "id": "example.plugin",
        "name": "Example",
        "author": {"githubLogin": "example", "displayName": "Example"},
        "description": "Example plugin",
        "repository": "https://github.com/example/plugin",
        "versions": [
            {
                "version": "1.0.0",
                "pclCoreVersion": "2026.08.1",
                "releaseNotes": "https://github.com/example/plugin/releases/tag/v1.0.0",
                "downloads": downloads,
            }
        ],
    }


def market_document(downloads: dict) -> dict:
    return {
        "version": 1,
        "updatedAt": None,
        "name": "Test",
        "group": "Test",
        "tags": [],
        "developers": [],
        "manifests": [],
        "plugins": [manifest(downloads)],
    }


class PluginPlatformDownloadTest(unittest.TestCase):
    def test_static_market_accepts_os_architecture_matrix(self) -> None:
        downloads = {
            operating_system: {
                architecture: download(f"{operating_system}-{architecture}")
                for architecture in ("amd64", "arm64")
            }
            for operating_system in ("windows", "linux", "macos")
        }
        validate_document(market_document(downloads))

    def test_static_market_keeps_legacy_downloads_compatible(self) -> None:
        validate_document(market_document({"anycpu": download("anycpu")}))

    def test_static_market_rejects_empty_or_unknown_groups(self) -> None:
        with self.assertRaises(PluginMarketError):
            validate_document(market_document({"linux": {}}))
        with self.assertRaises(PluginMarketError):
            validate_document(market_document({"freebsd": {"amd64": download("freebsd-amd64")}}))
        with self.assertRaises(PluginMarketError):
            validate_document(market_document({"linux": {"riscv64": download("linux-riscv64")}}))

    def test_topic_index_validates_nested_release_links(self) -> None:
        source = manifest({"linux": {"arm64": download("linux-arm64")}})
        result = validate_manifest(source, owner="example", repo="plugin", default_branch="main")
        self.assertEqual(source["versions"], result["versions"])

        invalid = copy.deepcopy(source)
        invalid["versions"][0]["downloads"]["linux"]["arm64"]["packageUrl"] = (
            "https://github.com/other/plugin/releases/download/v1.0.0/linux-arm64.pclx"
        )
        with self.assertRaises(IndexError):
            validate_manifest(invalid, owner="example", repo="plugin", default_branch="main")

    def test_static_market_validates_index_metadata(self) -> None:
        document = market_document({"anycpu": download("anycpu")})
        document["plugins"][0]["index"] = {
            "manifestUrl": "https://raw.githubusercontent.com/example/plugin/main/manifest.json",
            "lastUpdatedAt": "2026-08-25T10:00:00Z",
            "downloadCount": 42,
            "archived": False,
            "disabled": False,
            "fork": False,
        }
        validate_document(document)
        document["plugins"][0]["index"]["downloadCount"] = -1
        with self.assertRaises(PluginMarketError):
            validate_document(document)

    @patch("index_plugin_market.fetch_release_download_count", return_value=42)
    @patch("index_plugin_market.fetch_manifest_updated_at", return_value="2026-08-25T10:00:00Z")
    @patch(
        "index_plugin_market.fetch_readme_url",
        return_value="https://raw.githubusercontent.com/example/plugin/main/README.md",
    )
    @patch("index_plugin_market.fetch_manifest")
    @patch("index_plugin_market.search_topic")
    def test_build_document_preindexes_github_metadata(
        self,
        search_topic_mock,
        fetch_manifest_mock,
        _readme_mock,
        _updated_mock,
        _downloads_mock,
    ) -> None:
        search_topic_mock.return_value = [
            {
                "name": "plugin",
                "full_name": "example/plugin",
                "html_url": "https://github.com/example/plugin",
                "default_branch": "main",
                "archived": False,
                "disabled": False,
                "fork": False,
                "topics": ["pclnexplugin", "utility"],
                "owner": {
                    "login": "example",
                    "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
                },
            }
        ]
        fetch_manifest_mock.return_value = manifest({"anycpu": download("anycpu")})

        document, skipped = build_document(token="token", existing=None, developers=[])

        self.assertEqual([], skipped)
        plugin = document["plugins"][0]
        self.assertEqual(
            "https://raw.githubusercontent.com/example/plugin/main/manifest.json",
            plugin["index"]["manifestUrl"],
        )
        self.assertEqual(42, plugin["index"]["downloadCount"])
        self.assertEqual(
            "https://raw.githubusercontent.com/example/plugin/main/README.md",
            plugin["readmeUrl"],
        )
        self.assertEqual("https://avatars.githubusercontent.com/u/1?v=4", plugin["logo"])
        self.assertEqual(["utility"], plugin["tags"])


if __name__ == "__main__":
    unittest.main()
