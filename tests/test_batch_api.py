import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth.api import AuthConfig
from backend.auth.store import AuthStore

PASSWORD = "correct horse battery staple"


class BatchApiTest(unittest.TestCase):
    def test_queue_options_and_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "app.db"
            auth = AuthStore(db)
            auth.create_user("alice", PASSWORD)
            auth.create_user("bob", PASSWORD)
            with patch("backend.batch_scheduler.download_from_config", side_effect=RuntimeError("offline")):
                with TestClient(create_app(db, AuthConfig(cookie_secure=False), root)) as alice:
                    alice.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
                    csrf = {"X-CSRF-Token": alice.cookies.get("ducklingo_csrf")}
                    self.assertEqual(
                        alice.put("/api/batch/options", json={"quality": "720", "target_language": "English"}, headers=csrf).status_code,
                        200,
                    )
                    self.assertEqual(alice.get("/api/batch").json()["options"]["quality"], "720")
                    ids = alice.post("/api/batch", json={"urls": ["https://a", "nope"]}, headers=csrf).json()["ids"]
                    self.assertEqual(len(ids), 1)
                    items = []
                    for _ in range(100):
                        items = alice.get("/api/batch").json()["items"]
                        if items and items[0]["status"] == "error":
                            break
                        time.sleep(0.02)
                    self.assertEqual(items[0]["status"], "error")
                with TestClient(create_app(db, AuthConfig(cookie_secure=False), root)) as bob:
                    bob.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
                    bcsrf = {"X-CSRF-Token": bob.cookies.get("ducklingo_csrf")}
                    self.assertEqual(bob.get("/api/batch").json()["items"], [])
                    self.assertEqual(bob.post(f"/api/batch/{ids[0]}/retry", headers=bcsrf).status_code, 404)
                    self.assertEqual(bob.delete(f"/api/batch/{ids[0]}", headers=bcsrf).status_code, 404)


if __name__ == "__main__":
    unittest.main()
