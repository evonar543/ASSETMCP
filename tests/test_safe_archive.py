import tempfile
import unittest
import zipfile
from pathlib import Path

from assetmcp.server import _extract_zip


class SafeArchiveTests(unittest.TestCase):
    def test_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                _extract_zip(archive, root / "out")

    def test_blocks_suspicious_member(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("run.exe", "bad")
            with self.assertRaises(ValueError):
                _extract_zip(archive, root / "out")


if __name__ == "__main__":
    unittest.main()
