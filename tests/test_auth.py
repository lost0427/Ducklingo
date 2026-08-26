import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth.store import AuthStore


class AuthStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self.temp.name) / "auth.db")
        self.user_id = self.store.create_user("alice", "correct horse battery staple")

    def tearDown(self):
        self.temp.cleanup()

    def test_password_is_hashed_and_session_revokes(self):
        user = self.store.authenticate("alice", "correct horse battery staple", 5, 15)
        self.assertNotEqual(user["password_hash"], "correct horse battery staple")
        session, csrf = self.store.create_session(self.user_id, 120, 14)
        self.assertEqual(self.store.session_user(session, 120)["username"], "alice")
        self.assertTrue(self.store.csrf_valid(session, csrf))
        self.store.revoke(session)
        self.assertIsNone(self.store.session_user(session, 120))

    def test_bad_password_is_limited(self):
        for _ in range(5):
            self.assertIsNone(self.store.authenticate("alice", "wrong", 5, 15, "127.0.0.1"))
        self.assertIsNone(self.store.authenticate("alice", "correct horse battery staple", 5, 15))

    def test_http_csrf_and_password_change(self):
        client = TestClient(create_app(self.store.path))
        self.assertEqual(client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"}).status_code, 200)
        self.assertEqual(client.post("/api/auth/logout").status_code, 403)
        csrf = client.cookies.get("ducklingo_csrf")
        changed = client.post("/api/auth/password", headers={"X-CSRF-Token": csrf}, json={"current_password": "correct horse battery staple", "new_password": "new password that is long"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(client.get("/api/auth/me").headers["x-content-type-options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
