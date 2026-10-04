"""Verify deterministic tool discovery for portable and developer installs."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analog_fpv_compressor.runtime import discover_tools


class DiscoveryTests(unittest.TestCase):
    def test_frozen_tools_precede_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            suffix = ".exe" if os.name == "nt" else ""
            for name in ("ffmpeg", "ffprobe"):
                (root / "tools" / (name + suffix)).touch()
            with patch("analog_fpv_compressor.runtime.sys.frozen", True, create=True), \
                 patch("analog_fpv_compressor.runtime.sys.executable", str(root / "fpv-compress.exe")), \
                 patch.dict(os.environ, {}, clear=True), \
                 patch("analog_fpv_compressor.runtime.shutil.which", return_value="other-tool"):
                tools = discover_tools()
                self.assertEqual(Path(tools["ffmpeg"]).parent, root / "tools")

    def test_explicit_directory_precedes_portable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suffix = ".exe" if os.name == "nt" else ""
            for name in ("ffmpeg", "ffprobe"):
                (root / (name + suffix)).touch()
            with patch("analog_fpv_compressor.runtime.sys.frozen", True, create=True):
                self.assertEqual(Path(discover_tools(root)["ffprobe"]).parent, root)

    def test_incomplete_portable_pair_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            with patch("analog_fpv_compressor.runtime.sys.frozen", True, create=True), \
                 patch("analog_fpv_compressor.runtime.sys.executable", str(root / "fpv-compress.exe")), \
                 patch.dict(os.environ, {}, clear=True), \
                 patch("analog_fpv_compressor.runtime.shutil.which", side_effect=lambda name: "path/" + name):
                self.assertEqual(discover_tools(), {"ffmpeg": "path/ffmpeg", "ffprobe": "path/ffprobe"})


if __name__ == "__main__":
    unittest.main()
