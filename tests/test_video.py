import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth.api import AuthConfig
from backend.auth.store import AuthStore
from backend.video import download_video, format_selector
from backend.video_api import load_upload_config


class VideoDownloadTest(unittest.TestCase):
    def test_upload_config_reads_size_and_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "config.toml").write_text(
                '[upload]\nmax_file_size_gb = 0.5\nallowed_extensions = ["mp4", ".m4v"]\n',
                encoding="utf-8",
            )
            self.assertEqual(
                load_upload_config(root),
                {"max_bytes": 536870912, "extensions": ("mp4", "m4v")},
            )

    def test_quality_selector_is_bounded(self):
        self.assertEqual(format_selector("best"), "bestvideo+bestaudio/best")
        self.assertEqual(
            format_selector("1080"),
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        )
        with self.assertRaises(ValueError):
            format_selector("--output=/tmp/unsafe")

    def test_retries_until_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "video.mp4"
            failed = type("Result", (), {"returncode": 1, "stderr": "temporary"})()
            success = type("Result", (), {"returncode": 0, "stderr": ""})()
            calls = 0
            timeouts = []

            def run(*_args, **kwargs):
                nonlocal calls
                calls += 1
                timeouts.append(kwargs["timeout"])
                if calls == 1:
                    return failed
                output.write_bytes(b"ok")
                return success

            with patch("backend.video.subprocess.run", side_effect=run):
                output.write_bytes(b"partial")
                self.assertEqual(download_video("url", output, Path("yt"), Path("ffmpeg"), retry_delay=0), output)
                self.assertEqual(calls, 2)
                self.assertEqual(timeouts, [3600, 3600])

    def test_http_download_requires_auth_and_returns_video(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "auth.db"
            store = AuthStore(db_path)
            store.create_user("alice", "correct horse battery staple")
            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False)))

            self.assertEqual(
                client.post(
                    "/api/video/download",
                    json={"url": "https://example.com/video", "quality": "1080"},
                ).status_code,
                401,
            )
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            csrf = client.cookies.get("ducklingo_csrf")
            self.assertEqual(
                client.post(
                    "/api/video/download",
                    json={"url": "https://example.com/video", "quality": "1080"},
                ).status_code,
                403,
            )

            def fake_download(_url, output, **_kwargs):
                Path(output).write_bytes(b"video")
                return Path(output)

            with patch("backend.video_api.download_from_config", side_effect=fake_download):
                response = client.post(
                    "/api/video/download",
                    headers={"X-CSRF-Token": csrf},
                    json={"url": "https://example.com/video", "quality": "1080"},
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, b"video")
            self.assertEqual(response.headers["content-type"], "video/mp4")
            self.assertIn("ducklingo-video.mp4", response.headers["content-disposition"])

    def test_http_download_rejects_unknown_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "auth.db"
            store = AuthStore(db_path)
            store.create_user("alice", "correct horse battery staple")
            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False)))
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            csrf = client.cookies.get("ducklingo_csrf")
            response = client.post(
                "/api/video/download",
                headers={"X-CSRF-Token": csrf},
                json={"url": "https://example.com/video", "quality": "1080p"},
            )
            self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
