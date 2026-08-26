import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.video import download_video


class VideoDownloadTest(unittest.TestCase):
    def test_retries_until_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "video.mp4"
            failed = type("Result", (), {"returncode": 1, "stderr": "temporary"})()
            success = type("Result", (), {"returncode": 0, "stderr": ""})()
            calls = 0

            def run(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return failed
                output.write_bytes(b"ok")
                return success

            with patch("backend.video.subprocess.run", side_effect=run):
                output.write_bytes(b"partial")
                self.assertEqual(download_video("url", output, Path("yt"), Path("ffmpeg"), retry_delay=0), output)
                self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
