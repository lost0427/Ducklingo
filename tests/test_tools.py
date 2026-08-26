import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.tools import ensure_ytdlp


class ToolUpdateTest(unittest.TestCase):
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
