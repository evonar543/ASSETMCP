import json
import tempfile
import unittest
from pathlib import Path

from assetmcp.schemas import AssetResult
from assetmcp.services.license_checker import check_asset_license
from assetmcp.services.manifest import generate_credits_text, load_manifest, upsert_manifest_entry


class ManifestTests(unittest.TestCase):
    def test_manifest_and_credits_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest_path = root / "ASSET_MANIFEST.json"
            asset = AssetResult(
                id="openverse:tree",
                title="Tree",
                source_name="Openverse",
                source_url="https://example.com/tree",
                author="Artist",
                license="CC BY 4.0",
                license_url="https://creativecommons.org/licenses/by/4.0/",
            )
            check = check_asset_license(asset)
            entry = upsert_manifest_entry(manifest_path, asset, check, ["assets/tree.png"], checksum_sha256="abc")
            self.assertEqual(entry["id"], "openverse:tree")
            manifest = load_manifest(manifest_path)
            self.assertEqual(len(manifest["assets"]), 1)
            text = generate_credits_text(manifest)
            self.assertIn("Tree by Artist", text)
            json.loads(manifest_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
