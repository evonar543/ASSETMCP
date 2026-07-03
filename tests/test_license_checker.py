import unittest

from assetmcp.schemas import AssetResult
from assetmcp.services.license_checker import check_asset_license


class LicenseCheckerTests(unittest.TestCase):
    def test_allows_cc0(self):
        asset = AssetResult(
            id="kenney:test",
            title="Test Pack",
            source_name="Kenney",
            source_url="https://kenney.nl/assets/test",
            author="Kenney",
            license="CC0",
        )
        result = check_asset_license(asset)
        self.assertTrue(result.allowed)
        self.assertEqual(result.status, "allowed_public_domain")

    def test_blocks_unclear_license(self):
        asset = AssetResult(
            id="unknown:test",
            title="Mystery Pack",
            source_name="Unknown",
            source_url="https://example.com/asset",
        )
        result = check_asset_license(asset)
        self.assertFalse(result.allowed)
        self.assertEqual(result.status, "blocked_unclear_license")

    def test_allows_attribution_when_text_can_be_generated(self):
        asset = AssetResult(
            id="openverse:test",
            title="Tree",
            source_name="Openverse",
            source_url="https://example.com/tree",
            author="Artist",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
        )
        result = check_asset_license(asset)
        self.assertTrue(result.allowed)
        self.assertEqual(result.status, "allowed_with_attribution")
        self.assertIn("Tree by Artist", result.attribution_text)


if __name__ == "__main__":
    unittest.main()
