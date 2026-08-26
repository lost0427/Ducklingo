import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth.api import AuthConfig
from backend.auth.store import AuthStore


class AuthStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self.temp.name) / "auth.db")
        self.user_id = self.store.create_user("alice", "correct horse battery staple")

    def tearDown(self):
        self.temp.cleanup()

    def client(self) -> TestClient:
        return TestClient(create_app(self.store.path, AuthConfig(cookie_secure=False)))

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
        client = self.client()
        self.assertEqual(client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"}).status_code, 200)
        self.assertEqual(client.post("/api/auth/logout").status_code, 403)
        csrf = client.cookies.get("ducklingo_csrf")
        changed = client.post("/api/auth/password", headers={"X-CSRF-Token": csrf}, json={"current_password": "correct horse battery staple", "new_password": "new password that is long"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(client.get("/api/auth/me").headers["x-content-type-options"], "nosniff")

    def test_admin_can_create_user_and_duplicate_is_conflict(self):
        self.store.create_user("root", "correct horse battery staple", "admin")
        client = self.client()
        client.post("/api/auth/login", json={"username": "root", "password": "correct horse battery staple"})
        csrf = client.cookies.get("ducklingo_csrf")
        created = client.post("/api/auth/users", headers={"X-CSRF-Token": csrf}, json={"username": "bob", "password": "bobs password 1", "role": "user"})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["username"], "bob")
        self.assertNotIn("password_hash", created.json())
        again = client.post("/api/auth/users", headers={"X-CSRF-Token": csrf}, json={"username": "bob", "password": "bobs password 1", "role": "user"})
        self.assertEqual(again.status_code, 409)

    def test_non_admin_cannot_manage_users(self):
        client = self.client()
        client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
        csrf = client.cookies.get("ducklingo_csrf")
        self.assertEqual(client.get("/api/auth/users").status_code, 403)
        self.assertEqual(client.post("/api/auth/users", headers={"X-CSRF-Token": csrf}, json={"username": "bob", "password": "bobs password 1", "role": "user"}).status_code, 403)

    def test_deactivate_revokes_session_and_last_admin_stays(self):
        admin_id = self.store.create_user("root", "correct horse battery staple", "admin")
        admin = self.client()
        user = self.client()
        admin.post("/api/auth/login", json={"username": "root", "password": "correct horse battery staple"})
        user.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery staple"})
        self.assertEqual(user.get("/api/auth/me").status_code, 200)
        csrf = admin.cookies.get("ducklingo_csrf")
        stopped = admin.patch(f"/api/auth/users/{self.user_id}", headers={"X-CSRF-Token": csrf}, json={"is_active": False})
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(user.get("/api/auth/me").status_code, 401)
        blocked = admin.patch(f"/api/auth/users/{admin_id}", headers={"X-CSRF-Token": csrf}, json={"is_active": False})
        self.assertEqual(blocked.status_code, 400)

    def test_inactive_user_session_is_rejected(self):
        session, _csrf = self.store.create_session(self.user_id, 120, 14)
        with self.store._db() as db:
            db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.user_id,))
        self.assertIsNone(self.store.session_user(session, 120))

    def test_vite_origin_can_login(self):
        client = self.client()
        ok = client.post("/api/auth/login", headers={"Origin": "http://localhost:5173"}, json={"username": "alice", "password": "correct horse battery staple"})
        self.assertEqual(ok.status_code, 200, ok.text)


if __name__ == "__main__":
    unittest.main()
