import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .passwords import hash_password, verify_password


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init_db(self) -> None:
        with self._db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id_hash TEXT PRIMARY KEY,
                    csrf_hash TEXT NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS login_limits (
                    ip_hash TEXT PRIMARY KEY,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT
                );
            """)

    def create_user(self, username: str, password: str, role: str = "user") -> int:
        username = username.strip()
        if not username or len(password) < 12:
            raise ValueError("username and a 12-character password are required")
        if role not in ("user", "admin"):
            raise ValueError("role must be user or admin")
        try:
            with self._db() as db:
                cursor = db.execute(
                    "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (username, hash_password(password), role, _stamp(_now())),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("username exists")

    def user(self, username: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def user_by_id(self, user_id: int) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def authenticate(self, username: str, password: str, limit: int, lock_minutes: int, ip: str = "unknown") -> sqlite3.Row | None:
        user = self.user(username)
        now = _now()
        if self._ip_locked(ip, now):
            return None
        if not user or not user["is_active"]:
            return None
        if user["locked_until"] and datetime.fromisoformat(user["locked_until"]) > now:
            return None
        if not verify_password(user["password_hash"], password):
            attempts = user["failed_attempts"] + 1
            locked = _stamp(now + timedelta(minutes=lock_minutes)) if attempts >= limit else None
            with self._db() as db:
                db.execute("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?", (attempts, locked, user["id"]))
            self._record_ip_failure(ip, limit, lock_minutes, now)
            return None
        with self._db() as db:
            db.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE id = ?", (_stamp(now), user["id"]))
        self._clear_ip_failures(ip)
        return self.user_by_id(user["id"])

    def _ip_locked(self, ip: str, now: datetime) -> bool:
        with self._db() as db:
            row = db.execute("SELECT locked_until FROM login_limits WHERE ip_hash = ?", (_digest(ip),)).fetchone()
        return bool(row and row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > now)

    def _record_ip_failure(self, ip: str, limit: int, lock_minutes: int, now: datetime) -> None:
        key = _digest(ip)
        with self._db() as db:
            row = db.execute("SELECT failed_attempts FROM login_limits WHERE ip_hash = ?", (key,)).fetchone()
            attempts = (row["failed_attempts"] if row else 0) + 1
            locked = _stamp(now + timedelta(minutes=lock_minutes)) if attempts >= limit else None
            db.execute("INSERT INTO login_limits VALUES (?, ?, ?) ON CONFLICT(ip_hash) DO UPDATE SET failed_attempts = excluded.failed_attempts, locked_until = excluded.locked_until", (key, attempts, locked))

    def _clear_ip_failures(self, ip: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM login_limits WHERE ip_hash = ?", (_digest(ip),))

    def create_session(self, user_id: int, idle_minutes: int, max_days: int) -> tuple[str, str]:
        session = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = _now()
        with self._db() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, NULL)", (_digest(session), _digest(csrf), user_id, _stamp(now), _stamp(now), _stamp(now + timedelta(days=max_days))))
        return session, csrf

    def session_user(self, session: str, idle_minutes: int) -> sqlite3.Row | None:
        now = _now()
        with self._db() as db:
            row = db.execute(
                "SELECT sessions.*, users.is_active AS user_active FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.id_hash = ? AND sessions.revoked_at IS NULL",
                (_digest(session),),
            ).fetchone()
            if not row or not row["user_active"] or datetime.fromisoformat(row["expires_at"]) <= now or datetime.fromisoformat(row["last_seen_at"]) + timedelta(minutes=idle_minutes) <= now:
                return None
            db.execute("UPDATE sessions SET last_seen_at = ? WHERE id_hash = ?", (_stamp(now), row["id_hash"]))
        return self.user_by_id(row["user_id"])

    def csrf_valid(self, session: str, csrf: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT csrf_hash FROM sessions WHERE id_hash = ? AND revoked_at IS NULL", (_digest(session),)).fetchone()
        return bool(row and secrets.compare_digest(row["csrf_hash"], _digest(csrf)))

    def revoke(self, session: str) -> None:
        with self._db() as db:
            db.execute("UPDATE sessions SET revoked_at = ? WHERE id_hash = ?", (_stamp(_now()), _digest(session)))

    def change_password(self, user_id: int, password: str, keep_session: str | None = None) -> None:
        with self._db() as db:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
            query = "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL"
            values = [_stamp(_now()), user_id]
            if keep_session:
                query += " AND id_hash != ?"
                values.append(_digest(keep_session))
            db.execute(query, values)

    def list_users(self) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute("SELECT id, username, role, is_active, created_at, last_login_at FROM users ORDER BY id").fetchall()

    def active_admin_count(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1").fetchone()[0]

    def revoke_user_sessions(self, user_id: int) -> None:
        with self._db() as db:
            db.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (_stamp(_now()), user_id))

    def update_user(self, user_id: int, is_active: bool | None = None, password: str | None = None) -> sqlite3.Row:
        user = self.user_by_id(user_id)
        if not user:
            raise KeyError(user_id)
        if is_active is False and user["role"] == "admin" and user["is_active"] and self.active_admin_count() <= 1:
            raise ValueError("last admin")
        if password is not None:
            self.change_password(user_id, password)
        if is_active is not None:
            with self._db() as db:
                db.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(is_active), user_id))
            if not is_active:
                self.revoke_user_sessions(user_id)
        updated = self.user_by_id(user_id)
        if not updated:
            raise KeyError(user_id)
        return updated
