from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_pcl2_nex_releases import (  # noqa: E402
    CHANNELS,
    TARGETS,
    ReleaseAsset,
    ReleaseInfo,
    find_asset,
    select_release,
)


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


if __name__ == "__main__":
    unittest.main()
