import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth.api import AuthConfig
from backend.auth.store import AuthStore
from backend.media_store import MediaStore

PASSWORD = "correct horse battery staple"
REGISTRY = """
[[models]]
id = "ggml-base-en"
kind = "asr"
name = "Whisper base.en"
url = "https://example.com/ggml-base.en.bin"
filename = "ggml-base.en.bin"
"""


class AsrApiTest(unittest.TestCase):
    def build(self, files: dict[str, bytes] | None = None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "config").mkdir()
        (root / "config" / "asr_models.example.toml").write_text(REGISTRY, encoding="utf-8")
        db = root / "app.db"
        auth = AuthStore(db)
        auth.create_user("alice", PASSWORD)
        auth.create_user("bob", PASSWORD)
        auth.create_user("admin", PASSWORD, role="admin")
        clients = []
        for name in ("alice", "bob", "admin"):
            client = TestClient(create_app(db, AuthConfig(cookie_secure=False), root))
            client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
            clients.append(client)
        media = MediaStore(db)
        for key, content in (files or {}).items():
            user_dir, filename = key.split("/")
            target = root / "data" / "uploads" / user_dir
            target.mkdir(parents=True, exist_ok=True)
            (target / filename).write_bytes(content)
        return root, media, *clients

    @staticmethod
    def csrf(client) -> dict:
        return {"X-CSRF-Token": client.cookies.get("ducklingo_csrf")}

    def test_settings_roundtrip_masks_key(self):
        _, _, alice, _, admin = self.build()
        self.assertFalse(alice.get("/api/asr/settings").json()["asr"]["has_shared"])
        response = alice.put(
            "/api/asr/settings",
            json={"service": "asr", "values": {"kind": "cloud", "base_url": "https://api.mistral.ai", "api_key": "sk-1234567890abcdef", "model": "voxtral-mini-latest", "api_flavor": "mistral"}},
            headers=self.csrf(alice),
        )
        self.assertEqual(response.status_code, 200)
        own = alice.get("/api/asr/settings").json()["asr"]["own"]
        self.assertTrue(own["has_key"])
        self.assertEqual(own["api_key_masked"], "sk-…cdef")
        self.assertNotIn("api_key", own)
        admin.put(
            "/api/asr/settings",
            json={"service": "sentence", "scope": "shared", "values": {"base_url": "https://llm", "api_key": "gk-1234567890", "model": "m"}},
            headers=self.csrf(admin),
        )
        sentence = alice.get("/api/asr/settings").json()["sentence"]
        self.assertTrue(sentence["has_shared"])
        self.assertIsNone(sentence["shared"])
        self.assertIsNotNone(admin.get("/api/asr/settings").json()["sentence"]["shared"])

    def test_shared_scope_requires_admin(self):
        _, _, alice, _, _ = self.build()
        response = alice.put(
            "/api/asr/settings",
            json={"service": "asr", "scope": "shared", "values": {}},
            headers=self.csrf(alice),
        )
        self.assertEqual(response.status_code, 403)

    def test_put_rejects_unknown_fields(self):
        _, _, alice, _, _ = self.build()
        response = alice.put(
            "/api/asr/settings",
            json={"service": "asr", "values": {"hacker": True}},
            headers=self.csrf(alice),
        )
        self.assertEqual(response.status_code, 400)

    def test_start_requires_configuration_and_ownership(self):
        _, media, alice, bob, _ = self.build({"1/v.mp4": b"video"})
        file_id = "f" + "0" * 30 + "1"
        media.register(file_id, 1, "v.mp4", "upload", 5, "video/mp4")
        response = alice.post("/api/asr/start", json={"file_id": file_id}, headers=self.csrf(alice))
        self.assertEqual(response.status_code, 400)
        self.assertIn("ASR", response.json()["detail"])
        alice.put(
            "/api/asr/settings",
            json={"service": "asr", "values": {"kind": "cloud", "base_url": "https://x/v1", "api_key": "k", "model": "m"}},
            headers=self.csrf(alice),
        )
        alice.put(
            "/api/asr/settings",
            json={"service": "sentence", "values": {"base_url": "https://llm/v1", "api_key": "k", "model": "m"}},
            headers=self.csrf(alice),
        )
        self.assertEqual(
            bob.post("/api/asr/start", json={"file_id": file_id}, headers=self.csrf(bob)).status_code,
            404,
        )

    def test_translation_requires_explicit_provider(self):
        file_id = "f" + "0" * 30 + "1"
        root, media, alice, _, _ = self.build({f"1/{file_id}.mp4": b"video"})
        media.register(file_id, 1, "v.mp4", "upload", 5, "video/mp4")
        headers = self.csrf(alice)
        alice.put("/api/asr/settings", json={"service": "asr", "values": {"kind": "cloud", "base_url": "https://x/v1", "api_key": "k", "model": "m"}}, headers=headers)
        alice.put("/api/asr/settings", json={"service": "sentence", "values": {"base_url": "https://llm/v1", "api_key": "k", "model": "m"}}, headers=headers)
        response = alice.post("/api/asr/start", json={"file_id": file_id, "target_language": "Chinese"}, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Translation", response.json()["detail"])

    def test_models_list_and_admin_only_download(self):
        _, _, alice, _, admin = self.build()
        models = alice.get("/api/asr/models").json()
        self.assertEqual([m["id"] for m in models], ["ggml-base-en"])
        self.assertFalse(models[0]["installed"])
        self.assertEqual(
            alice.post("/api/asr/models/download", json={"id": "ggml-base-en"}, headers=self.csrf(alice)).status_code,
            403,
        )
        self.assertEqual(
            admin.post("/api/asr/models/download", json={"id": "nope"}, headers=self.csrf(admin)).status_code,
            400,
        )

    def test_reset_removes_artifacts_keeps_media(self):
        file_id = "f" + "0" * 30 + "1"
        root, media, alice, _, _ = self.build(
            {
                f"1/{file_id}.mp4": b"video",
                f"1/{file_id}.srt": b"srt",
                f"1/{file_id}.asr.json": b"{}",
                f"1/{file_id}.asr-0.mp3": b"chunk",
            }
        )
        media.register(file_id, 1, "movie.mp4", "upload", 5, "video/mp4")
        user_dir = root / "data" / "uploads" / "1"
        self.assertEqual(
            alice.post("/api/asr/reset/" + file_id, headers=self.csrf(alice)).status_code,
            200,
        )
        remaining = sorted(path.name for path in user_dir.glob(file_id + ".*"))
        self.assertEqual(remaining, [file_id + ".mp4"])
        self.assertEqual(alice.get(f"/api/asr/status/{file_id}").json()["status"], "idle")

    def test_end_to_end_job_produces_srt(self):
        file_id = "e" + "0" * 30 + "1"
        root, media, alice, _, _ = self.build({f"1/{file_id}.mp4": b"video"})
        media.register(file_id, 1, "movie.mp4", "upload", 5, "video/mp4")
        alice.put(
            "/api/asr/settings",
            json={"service": "asr", "values": {"kind": "cloud", "base_url": "https://x/v1", "api_key": "k", "model": "m"}},
            headers=self.csrf(alice),
        )
        alice.put(
            "/api/asr/settings",
            json={"service": "sentence", "values": {"base_url": "https://llm/v1", "api_key": "k", "model": "m"}},
            headers=self.csrf(alice),
        )

        async def fake_cloud(report, source, workdir, fid, asr_settings, asr_section, ffmpeg):
            report("transcribe")
            return [
                {"text": "hello", "start": 0.0, "end": 0.5},
                {"text": "world", "start": 0.5, "end": 1.0},
            ]

        async def fake_split(report, words, sentence_settings, sentence_section, checkpoint, timeout=300.0):
            return [{"text": "hello world", "start": 0.0, "end": 10.0}]

        with patch("backend.asr_api.transcribe_cloud", fake_cloud), patch(
            "backend.asr_api.split_sentences", fake_split
        ), patch("backend.asr_api.resolve_ffmpeg", return_value=Path("ffmpeg")), patch(
            "backend.asr_api.probe_duration", return_value=5.0
        ):
            with TestClient(create_app(root / "app.db", AuthConfig(cookie_secure=False), root)) as client:
                client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
                response = client.post("/api/asr/start", json={"file_id": file_id}, headers=self.csrf(client))
                self.assertEqual(response.status_code, 200)
                status = {}
                for _ in range(100):
                    status = client.get(f"/api/asr/status/{file_id}").json()
                    if status["status"] in ("done", "error"):
                        break
                    time.sleep(0.05)
                self.assertEqual(status["status"], "done", status)
                self.assertTrue(status["has_srt"])
                srt = client.get(f"/api/asr/srt/{file_id}")
                self.assertEqual(srt.status_code, 200)
                self.assertIn("hello world", srt.text)
                self.assertIn("00:00:05,000", srt.text)
                self.assertEqual(
                    client.post(f"/api/asr/cancel/{file_id}", headers=self.csrf(client)).status_code,
                    200,
                )

    def test_start_uses_source_extension_not_asr_mp3(self):
        file_id = "a" + "0" * 30 + "1"
        root, media, alice, _, _ = self.build({
            f"1/{file_id}.mp3": b"source",
            f"1/{file_id}.asr.mp3": b"extracted",
        })
        media.register(file_id, 1, "movie.mp3", "upload", 6, "audio/mpeg")
        alice.put(
            "/api/asr/settings",
            json={"service": "asr", "values": {"kind": "cloud", "base_url": "https://x/v1", "api_key": "k", "model": "m"}},
            headers=self.csrf(alice),
        )
        alice.put(
            "/api/asr/settings",
            json={"service": "sentence", "values": {"base_url": "https://llm/v1", "api_key": "k", "model": "m"}},
            headers=self.csrf(alice),
        )
        seen = {}

        async def fake_cloud(report, source, workdir, fid, asr_settings, asr_section, ffmpeg):
            seen["source"] = source
            return [{"text": "hello", "start": 0.0, "end": 0.5}]

        async def fake_split(report, words, sentence_settings, sentence_section, checkpoint, timeout=300.0):
            return [{"text": "hello", "start": 0.0, "end": 1.0}]

        with patch("backend.asr_api.transcribe_cloud", fake_cloud), patch(
            "backend.asr_api.split_sentences", fake_split
        ), patch("backend.asr_api.resolve_ffmpeg", return_value=Path("ffmpeg")), patch(
            "backend.asr_api.probe_duration", return_value=0.5
        ):
            self.assertEqual(
                alice.post("/api/asr/start", json={"file_id": file_id}, headers=self.csrf(alice)).status_code,
                200,
            )
            for _ in range(100):
                status = alice.get(f"/api/asr/status/{file_id}").json()
                if status["status"] in ("done", "error"):
                    break
                time.sleep(0.05)
        self.assertEqual(status["status"], "done", status)
        self.assertEqual(seen["source"], root / "data" / "uploads" / "1" / f"{file_id}.mp3")

    def test_translation_flow_exposes_archive_and_progress(self):
        file_id = "d" + "0" * 30 + "1"
        root, media, alice, _, _ = self.build({f"1/{file_id}.mp4": b"video"})
        media.register(file_id, 1, "movie.mp4", "upload", 5, "video/mp4")
        settings = {"kind": "cloud", "base_url": "https://x/v1", "api_key": "k", "model": "m"}
        alice.put("/api/asr/settings", json={"service": "asr", "values": settings}, headers=self.csrf(alice))
        alice.put("/api/asr/settings", json={"service": "sentence", "values": {"base_url": "https://llm/v1", "api_key": "k", "model": "split"}}, headers=self.csrf(alice))
        alice.put("/api/asr/settings", json={"service": "translate", "values": {"base_url": "https://llm/v1", "api_key": "k", "model": "trans"}}, headers=self.csrf(alice))

        async def fake_cloud(report, source, workdir, fid, asr_settings, asr_section, ffmpeg):
            return [{"text": "hello", "start": 0.0, "end": 0.5}]

        async def fake_split(report, words, sentence_settings, sentence_section, checkpoint, timeout=300.0):
            return [{"text": "hello", "start": 0.0, "end": 0.5}]

        async def fake_translate(report, sentences, translate_settings, translate_section, checkpoint, **kwargs):
            checkpoint.write_text(json.dumps({"target_language": kwargs["target_language"], "mode": kwargs["mode"], "reasoning_effort": kwargs["reasoning_effort"], "context_window_tokens": 131072, "max_context_ratio": 0.8, "batches": [{"done": True, "translations": {"1": "hello"}}]}), "utf-8")
            return [{**sentences[0], "id": "1", "translation": "hello"}]

        def fake_archive(directory, sentences):
            archive = directory / "20260904_143210_subtitles.zip"
            archive.write_bytes(b"zip")
            return archive

        with patch("backend.asr_api.transcribe_cloud", fake_cloud), patch("backend.asr_api.split_sentences", fake_split), patch("backend.asr_api.translate_sentences", fake_translate), patch("backend.asr_api.create_archive", fake_archive), patch("backend.asr_api.resolve_ffmpeg", return_value=Path("ffmpeg")), patch("backend.asr_api.probe_duration", return_value=5.0):
            with TestClient(create_app(root / "app.db", AuthConfig(cookie_secure=False), root)) as client:
                client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
                self.assertEqual(client.post("/api/asr/start", json={"file_id": file_id, "target_language": "Chinese", "reasoning_effort": "high"}, headers=self.csrf(client)).status_code, 200)
                for _ in range(100):
                    status = client.get(f"/api/asr/status/{file_id}").json()
                    if status["status"] in ("done", "error"):
                        break
                    time.sleep(0.05)
                self.assertEqual(status["status"], "done", status)
                self.assertEqual((status["completed_batches"], status["total_batches"], status["target_language"], status["reasoning_effort"]), (1, 1, "Chinese", "high"))
                self.assertTrue(status["has_archive"])
                self.assertEqual(client.get(f"/api/asr/archive/{file_id}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
