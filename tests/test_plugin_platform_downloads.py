from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from index_plugin_market import IndexError, validate_manifest  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
