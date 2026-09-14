import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.tools import download_file, ensure_ffmpeg, ensure_ytdlp


class TruncatedResponse:
    headers = {"Content-Length": "10"}

    def __init__(self):
        self._chunks = [b"12345"]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self._chunks.pop(0) if self._chunks else b""


class DownloadFileTest(unittest.TestCase):
    def test_incomplete_download_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.gguf"
            with patch("urllib.request.urlopen", return_value=TruncatedResponse()):
                with self.assertRaises(OSError):
                    download_file("https://example.com/model.gguf", target)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".gguf.part").exists())


class ToolUpdateTest(unittest.TestCase):
    def test_existing_ffmpeg_skips_release_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "ffmpeg" / "tag" / "bin" / "ffmpeg.exe"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"binary")
            with patch("backend.tools._ffmpeg_asset") as lookup:
                self.assertEqual(ensure_ffmpeg(Path(directory), "tag"), binary)
            lookup.assert_not_called()

    def test_existing_ytdlp_uses_self_update(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "yt-dlp.exe"
            target.write_bytes(b"binary")
            result = type("Result", (), {"returncode": 0, "stderr": ""})()
            with patch("backend.tools._platform", return_value="windows"):
                with patch("backend.tools.subprocess.run", return_value=result) as run:
                    self.assertEqual(ensure_ytdlp(Path(directory)), target)
            run.assert_called_once_with([str(target), "-U"], text=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
