import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth.api import AuthConfig
from backend.auth.store import AuthStore
from backend.media_store import MediaStore, sanitize_filename
from backend.video import download_video, format_selector
from backend.video_api import load_upload_config
from backend.workflow import untrack_url


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

    def test_http_download_persists_and_serves_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            AuthStore(db_path).create_user("bob", "correct horse battery staple")
            alice = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            bob = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            self.assertEqual(
                alice.post(
                    "/api/video/download",
                    json={"url": "https://example.com/video", "quality": "1080"},
                ).status_code,
                401,
            )
            alice.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            bob.post("/api/auth/login", json={"username": "bob", "password": "correct horse battery staple"})
            csrf = alice.cookies.get("ducklingo_csrf")
            self.assertEqual(
                alice.post(
                    "/api/video/download",
                    json={"url": "https://example.com/video", "quality": "1080"},
                ).status_code,
                403,
            )

            def fake_download(_url, output, **_kwargs):
                Path(output).write_bytes(b"video")
                return Path(output)

            with patch("backend.video_api.download_from_config", side_effect=fake_download):
                response = alice.post(
                    "/api/video/download",
                    headers={"X-CSRF-Token": csrf},
                    json={"url": "https://example.com/video", "quality": "1080"},
                )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertTrue(payload["filename"].startswith("ducklingo-"))
            files = alice.get("/api/video/files").json()
            self.assertEqual([row["id"] for row in files], [payload["id"]])
            served = alice.get(f"/api/video/file/{payload['id']}")
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served.content, b"video")

            intruded = bob.get(f"/api/video/file/{payload['id']}")
            self.assertEqual(intruded.status_code, 404)
            self.assertIsNone(files[0]["thumbnail"])
            self.assertEqual(bob.get(f"/api/video/thumb/{payload['id']}").status_code, 404)
            self.assertEqual(alice.get(f"/api/video/thumb/{payload['id']}").status_code, 404)
            self.assertEqual(files[0]["sourceUrl"], "https://example.com/video")

    def test_download_cover_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            csrf = client.cookies.get("ducklingo_csrf")

            def fake_download(_url, output, **_kwargs):
                output = Path(output)
                output.write_bytes(b"video")
                output.with_suffix(".jpg").write_bytes(b"jpeg-data")
                return output

            with patch("backend.video_api.download_from_config", side_effect=fake_download):
                response = client.post(
                    "/api/video/download",
                    headers={"X-CSRF-Token": csrf},
                    json={"url": "https://example.com/video", "quality": "1080"},
                )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["thumbnail"], f"/api/video/thumb/{payload['id']}")

            thumb = client.get(payload["thumbnail"])
            self.assertEqual(thumb.status_code, 200)
            self.assertEqual(thumb.content, b"jpeg-data")

            deleted = client.delete(f"/api/video/upload/{payload['id']}", headers={"X-CSRF-Token": csrf})
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertFalse((root / "data" / "uploads" / "1" / f"{payload['id']}.jpg").exists())
            self.assertEqual(client.get(payload["thumbnail"]).status_code, 404)

    def test_http_download_uses_ytdlp_title(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            csrf = client.cookies.get("ducklingo_csrf")

            def fake_download(_url, output, **_kwargs):
                output = Path(output)
                output.write_bytes(b"video")
                Path(f"{output}.info.json").write_text('{"title": "Bad<title>: math 课"}', encoding="utf-8")
                return output

            with patch("backend.video_api.download_from_config", side_effect=fake_download):
                response = client.post(
                    "/api/video/download",
                    headers={"X-CSRF-Token": csrf},
                    json={"url": "https://example.com/video", "quality": "1080"},
                )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["filename"], "Badtitle_math_课.mp4")

            stored = root / "data" / "uploads" / "1" / f"{payload['id']}.mp4"
            self.assertTrue(stored.exists())
            self.assertFalse(Path(f"{stored}.info.json").exists())
            served = client.get(f"/api/video/file/{payload['id']}")
            self.assertEqual(served.content, b"video")

    def test_http_download_rejects_unknown_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            csrf = client.cookies.get("ducklingo_csrf")
            response = client.post(
                "/api/video/download",
                headers={"X-CSRF-Token": csrf},
                json={"url": "https://example.com/video", "quality": "1080p"},
            )
            self.assertEqual(response.status_code, 422)

    def test_upload_streams_file_and_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            store = AuthStore(db_path)
            store.create_user("alice", "correct horse battery staple")
            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            csrf = client.cookies.get("ducklingo_csrf")

            response = client.post(
                "/api/video/upload?filename=lesson.mp4",
                headers={"Content-Type": "video/mp4", "X-CSRF-Token": csrf},
                content=b"video data",
            )

            self.assertEqual(response.status_code, 200, response.text)
            uploaded = response.json()
            stored = root / "data" / "uploads" / "1" / f"{uploaded['id']}.mp4"
            self.assertEqual(stored.read_bytes(), b"video data")
            self.assertEqual(uploaded["size"], 10)
            deleted = client.delete(f"/api/video/upload/{uploaded['id']}", headers={"X-CSRF-Token": csrf})
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertFalse(stored.exists())

    def test_upload_requires_csrf_and_enforces_configured_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "config.toml").write_text(
                '[upload]\nmax_file_size_gb = 0.00000001\nallowed_extensions = ["mp4"]\n',
                encoding="utf-8",
            )
            db_path = root / "auth.db"
            store = AuthStore(db_path)
            store.create_user("alice", "correct horse battery staple")
            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})

            missing_csrf = client.post("/api/video/upload?filename=lesson.mp4", content=b"x")
            self.assertEqual(missing_csrf.status_code, 403)
            csrf = client.cookies.get("ducklingo_csrf")
            too_large = client.post(
                "/api/video/upload?filename=lesson.mp4",
                headers={"X-CSRF-Token": csrf},
                content=b"x" * 20,
            )
            self.assertEqual(too_large.status_code, 413)

    def test_upload_files_are_isolated_between_users(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            store = AuthStore(db_path)
            store.create_user("alice", "correct horse battery staple")
            store.create_user("bob", "correct horse battery staple")
            alice = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            bob = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            alice.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            bob.post("/api/auth/login", json={"username": "bob", "password": "correct horse battery staple"})
            alice_csrf = alice.cookies.get("ducklingo_csrf")
            bob_csrf = bob.cookies.get("ducklingo_csrf")
            uploaded = alice.post(
                "/api/video/upload?filename=lesson.mp4",
                headers={"X-CSRF-Token": alice_csrf},
                content=b"alice video",
            ).json()

            response = bob.delete(
                f"/api/video/upload/{uploaded['id']}",
                headers={"X-CSRF-Token": bob_csrf},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue((root / "data" / "uploads" / "1" / f"{uploaded['id']}.mp4").exists())


class MediaStoreTest(unittest.TestCase):
    def test_sanitize_filename_keeps_cjk_and_strips_unsafe(self):
        cases = {
            "数学课 第3讲.mp4": "数学课_第3讲.mp4",
            'a/b\\c:d*e?f"g<h>i|j.mp4': "abcdefghij.mp4",
            "messy\x07 name..mp4": "messy_name.mp4",
            ".hidden": "hidden",
            "CON.mp4": "_CON.mp4",
            "": "media",
            "   ": "media",
        }
        for source, expected in cases.items():
            self.assertEqual(sanitize_filename(source), expected)
        long = sanitize_filename("片段" * 200 + ".mp4")
        self.assertLessEqual(len(long), 120)
        self.assertTrue(long.endswith(".mp4"))

    def test_upload_registers_metadata_and_delete_removes_both(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            csrf = client.cookies.get("ducklingo_csrf")

            response = client.post(
                "/api/video/upload?filename=数学课 第3讲.mp4",
                headers={"Content-Type": "video/mp4", "X-CSRF-Token": csrf},
                content=b"video data",
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["filename"], "数学课_第3讲.mp4")

            media = MediaStore(db_path)
            rows = media.list_for_user(1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "upload")
            self.assertEqual(rows[0]["size"], 10)

            deleted = client.delete(f"/api/video/upload/{response.json()['id']}", headers={"X-CSRF-Token": csrf})
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(media.list_for_user(1), [])
            self.assertEqual(client.get("/api/video/files").json(), [])

    def test_new_media_replaces_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            csrf = client.cookies.get("ducklingo_csrf")

            def upload(name: str, body: bytes):
                return client.post(
                    f"/api/video/upload?filename={name}",
                    headers={"Content-Type": "video/mp4", "X-CSRF-Token": csrf},
                    content=body,
                )

            first = upload("lesson.mp4", b"one")
            second = upload("lesson.mp4", b"twotwo")
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(second.status_code, 200, second.text)

            media = MediaStore(db_path)
            rows = media.list_for_user(1)
            self.assertEqual([row["id"] for row in rows], [second.json()["id"]])
            user_dir = root / "data" / "uploads" / "1"
            self.assertEqual([path.name for path in user_dir.iterdir()], [f"{second.json()['id']}.mp4"])

    def test_source_url_column_migrates_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            legacy = sqlite3.connect(db_path)
            legacy.executescript("""
                CREATE TABLE files (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    content_type TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
            """)
            legacy.execute("INSERT INTO files VALUES (?, 1, ?, ?, ?, ?, ?)", ("a" * 32, "legacy.mp4", "upload", 4, "video/mp4", "2026-01-01T00:00:00+00:00"))
            legacy.commit()
            legacy.close()

            client = TestClient(create_app(db_path, AuthConfig(cookie_secure=False), root))
            client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
            files = client.get("/api/video/files").json()
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0]["sourceUrl"], "")

    def test_reconcile_keeps_cover_and_sweeps_orphans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            user_dir = root / "data" / "uploads" / "1"
            user_dir.mkdir(parents=True)
            (user_dir / ("f" * 32 + ".mp4")).write_bytes(b"media")
            (user_dir / ("f" * 32 + ".jpg")).write_bytes(b"cover")
            (user_dir / ("a" * 32 + ".jpg")).write_bytes(b"orphan-cover")

            create_app(db_path, AuthConfig(cookie_secure=False), root)

            media = MediaStore(db_path)
            rows = media.list_for_user(1)
            self.assertEqual([row["id"] for row in rows], ["f" * 32])
            self.assertEqual(
                sorted(path.name for path in user_dir.iterdir()),
                sorted(["f" * 32 + ".mp4", "f" * 32 + ".jpg"]),
            )

    def test_reconcile_collapses_to_newest_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            user_dir = root / "data" / "uploads" / "1"
            user_dir.mkdir(parents=True)
            (user_dir / ("a" * 32 + ".mp4")).write_bytes(b"older")
            (user_dir / ("f" * 32 + ".mp4")).write_bytes(b"newer")

            create_app(db_path, AuthConfig(cookie_secure=False), root)

            media = MediaStore(db_path)
            rows = media.list_for_user(1)
            self.assertEqual([row["id"] for row in rows], ["f" * 32])
            self.assertEqual([path.name for path in user_dir.iterdir()], ["f" * 32 + ".mp4"])

    def test_reconcile_registers_orphans_and_drops_missing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "auth.db"
            AuthStore(db_path).create_user("alice", "correct horse battery staple")
            orphan_id = uuid4().hex
            user_dir = root / "data" / "uploads" / "1"
            user_dir.mkdir(parents=True)
            (user_dir / f"{orphan_id}.mp4").write_bytes(b"legacy")
            (user_dir / "leftover.part").write_bytes(b"partial")
            media = MediaStore(db_path)
            media.register(uuid4().hex, 1, "ghost.mp4", "upload", 5)

            create_app(db_path, AuthConfig(cookie_secure=False), root)

            rows = media.list_for_user(1)
            self.assertEqual([row["id"] for row in rows], [orphan_id])
            self.assertEqual(rows[0]["filename"], f"{orphan_id}.mp4")
            self.assertFalse((user_dir / "leftover.part").exists())


class UntrackUrlTest(unittest.TestCase):
    def test_untrack_url_cleans_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "config.toml").write_text('[tools]\ndata_dir = "data/tools"\n', encoding="utf-8")
            binary = root / "untracker.exe"
            binary.write_bytes(b"")
            result = type(
                "Result",
                (),
                {"returncode": 0, "stdout": '{"output":"https://x.com/a"}', "stderr": ""},
            )()
            with (
                patch("backend.workflow.ensure_untracker", return_value=binary),
                patch("backend.workflow.subprocess.run", return_value=result) as run,
            ):
                self.assertEqual(untrack_url("https://x.com/a?s=20", root), "https://x.com/a")
            self.assertEqual(
                run.call_args.args[0],
                [str(binary), "--json", "--timeout", "5", "https://x.com/a?s=20"],
            )

    def test_untrack_url_falls_back_on_tool_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("backend.workflow.ensure_untracker", side_effect=RuntimeError("no binary")):
                self.assertEqual(untrack_url("https://x.com/a?s=20", Path(directory)), "https://x.com/a?s=20")

    def test_untrack_url_falls_back_without_config(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(untrack_url("https://x.com/a?s=20", Path(directory)), "https://x.com/a?s=20")


if __name__ == "__main__":
    unittest.main()
