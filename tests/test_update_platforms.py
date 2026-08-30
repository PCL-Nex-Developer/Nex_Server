from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_pcl2_nex_releases import (  # noqa: E402
    CHANNELS,
    TARGETS,
    ReleaseAsset,
    ReleaseInfo,
    build_download_urls,
    build_release_manifest,
    find_asset,
    ensure_empty_update_file,
    is_update_current,
    modelscope_download_url,
    select_release,
)
from static_api import validate_release_document  # noqa: E402


def release(*asset_names: str, prerelease: bool = True) -> ReleaseInfo:
    return ReleaseInfo(
        tag_name="v2026.08.4",
        name="test",
        prerelease=prerelease,
        published_at="2026-08-26T14:14:48Z",
        body="test",
        assets=[ReleaseAsset(name=name, browser_download_url=f"https://example.test/{name}") for name in asset_names],
    )


class UpdatePlatformTest(unittest.TestCase):
    def test_normalized_release_names_are_selected_for_every_target(self) -> None:
        for target, target_info in TARGETS.items():
            name = f"PCL2_Nex_Beta_{target_info['runtime']}{target_info['extension']}"
            self.assertEqual(name, find_asset(release(name), "Beta", target_info).name, target)

    def test_transition_release_names_remain_supported(self) -> None:
        assets = release(
            "PCL2_Nex_Beta_x64.exe",
            "PCL2_Nex_Beta_ARM64.exe",
            "pcl2-2026.08.4-x86_64.AppImage",
            "PCL2-macOS-x64.dmg",
            "PCL2-macOS-arm64.dmg",
        )
        for target_info in TARGETS.values():
            self.assertIsNotNone(find_asset(assets, "Beta", target_info))

    def test_release_selection_is_independent_per_platform(self) -> None:
        mac_release = release("PCL2-macOS-arm64.dmg")
        self.assertIs(mac_release, select_release([mac_release], CHANNELS["beta"], TARGETS["osx-arm64"]))
        self.assertIsNone(select_release([mac_release], CHANNELS["beta"], TARGETS["linux-x64"]))

    def test_missing_platform_release_gets_an_empty_feed_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "updates-sr-osx-x64.json"
            self.assertTrue(ensure_empty_update_file(path))
            self.assertEqual('{"assets":[]}\n', path.read_text(encoding="utf-8"))
            self.assertFalse(ensure_empty_update_file(path))

    def test_modelscope_download_is_stable_and_preferred(self) -> None:
        asset = ReleaseAsset(
            name="PCL2_Nex_Beta_win-x64.exe",
            browser_download_url="https://github.example/PCL2_Nex_Beta_win-x64.exe",
        )
        current = release(asset.name)
        downloads = build_download_urls(
            asset=asset,
            release=current,
            configuration="Beta",
            target_info=TARGETS["win-x64"],
            mirror_available=True,
        )
        self.assertEqual(
            modelscope_download_url(current.tag_name, asset.name),
            downloads[0],
        )
        self.assertEqual(asset.browser_download_url, downloads[1])

    def test_update_requires_the_complete_ordered_fallback_list(self) -> None:
        github = "https://github.example/file.exe"
        modelscope = "https://modelscope.example/file.exe"
        asset = {"sha256": "a" * 64, "downloads": [github]}
        self.assertFalse(
            is_update_current(
                asset,
                expected_sha256="a" * 64,
                expected_downloads=[modelscope, github],
            )
        )

    def test_release_manifest_exposes_named_download_sources(self) -> None:
        assets = []
        for target_info in TARGETS.values():
            name = f"PCL2_Nex_Beta_{target_info['runtime']}{target_info['extension']}"
            assets.append(ReleaseAsset(name=name, browser_download_url=f"https://github.example/{name}"))
        current = ReleaseInfo(
            tag_name="v2026.08.4",
            name="test",
            prerelease=True,
            published_at="2026-08-26T14:14:48Z",
            body="test",
            assets=assets,
        )
        manifest = build_release_manifest([current], {current.tag_name: True})
        beta = manifest["channels"]["beta"]
        self.assertEqual(current.tag_name, beta["tag"])
        self.assertEqual(5, len(beta["assets"]))
        self.assertIn("modelscope", beta["assets"][0]["downloads"])
        self.assertIn("github", beta["assets"][0]["downloads"])

    def test_release_manifest_rejects_temporary_modelscope_urls(self) -> None:
        manifest = {
            "schema_version": 1,
            "channels": {
                "beta": {
                    "tag": "v2026.08.4",
                    "version": "2026.08.4",
                    "modelscope_url": "https://www.modelscope.cn/datasets/example/tree/master/releases/v2026.08.4",
                    "assets": [
                        {
                            "target": "win-x64",
                            "format": "exe",
                            "file_name": "PCL2_Nex_Beta_win-x64.exe",
                            "downloads": {
                                "github": "https://github.example/file.exe",
                                "modelscope": "https://cdn.example/file.exe?auth_key=temporary",
                            },
                        }
                    ],
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "releases.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unstable ModelScope URL"):
                validate_release_document(path)


if __name__ == "__main__":
    unittest.main()
