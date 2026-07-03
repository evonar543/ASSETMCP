import asyncio
import tempfile
import unittest
from pathlib import Path

from assetmcp.providers.registry import search_providers
from assetmcp.schemas import AssetResult
from assetmcp.services.scaffolder import scaffold_game_project


async def ok_provider(query: str, max_results: int):
    return [
        AssetResult(
            id="ok:1",
            title=query,
            source_name="ok",
            source_url="https://example.com",
            license="CC0",
        )
    ]


async def failing_provider(query: str, max_results: int):
    raise RuntimeError("provider down")


class ProviderAndScaffoldTests(unittest.TestCase):
    def test_provider_failures_are_non_fatal(self):
        result = asyncio.run(
            search_providers(
                "tree",
                {"ok": ok_provider, "bad": failing_provider},
                max_results_per_source=1,
            )
        )
        self.assertEqual(len(result["results"]), 1)
        self.assertIn("error", result["by_source"]["bad"])

    def test_canvas_scaffold(self):
        with tempfile.TemporaryDirectory() as temp:
            result = scaffold_game_project(Path(temp) / "game", "html", "Test Game")
            self.assertTrue((Path(result["project_path"]) / "index.html").exists())
            self.assertTrue((Path(result["project_path"]) / "ASSET_MANIFEST.json").exists())


if __name__ == "__main__":
    unittest.main()
