import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SERVICES = ("asr", "sentence", "translate")
FIELDS = ("kind", "base_url", "api_key", "model", "api_flavor", "auth_style", "aligner", "config_source")
GLOBAL = 0


def mask_key(key: str) -> str:
    return f"{key[:3]}…{key[-4:]}" if len(key) > 8 else "…"


class SettingsStore:
    """provider_settings: user_id=0 is the admin shared row; user rows override per field."""

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
                CREATE TABLE IF NOT EXISTS provider_settings (
                    user_id INTEGER NOT NULL,
                    service TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL DEFAULT '',
                    api_key TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    api_flavor TEXT NOT NULL DEFAULT '',
                    auth_style TEXT NOT NULL DEFAULT '',
                    aligner TEXT NOT NULL DEFAULT '',
                    config_source TEXT NOT NULL DEFAULT 'auto',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, service)
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(provider_settings)")}
            if "config_source" not in columns:
                db.execute("ALTER TABLE provider_settings ADD COLUMN config_source TEXT NOT NULL DEFAULT 'auto'")

    def put(self, user_id: int, service: str, values: dict) -> None:
        if service not in SERVICES:
            raise ValueError(f"unknown service: {service}")
        unknown = set(values) - set(FIELDS)
        if unknown:
            raise ValueError(f"unknown settings fields: {sorted(unknown)}")
        stamp = datetime.now(timezone.utc).isoformat()
        row = [str(values.get(name, "") or "") for name in FIELDS]
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO provider_settings"
                f" (user_id, service, {', '.join(FIELDS)}, updated_at)"
                f" VALUES (?, ?, {', '.join('?' for _ in FIELDS)}, ?)",
                (user_id, service, *row, stamp),
            )

    def get(self, user_id: int, service: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute(
                "SELECT * FROM provider_settings WHERE user_id = ? AND service = ?",
                (user_id, service),
            ).fetchone()

    def resolve(self, user_id: int, service: str) -> dict | None:
        merged: dict[str, str] = {}
        own = self.get(user_id, service)
        shared = self.get(GLOBAL, service)
        if own is not None and own["config_source"] == "shared":
            rows = (shared,)
        elif own is not None and own["config_source"] == "personal":
            rows = (own,)
        else:
            rows = (shared, own)
        for row in rows:
            if row is None:
                continue
            for name in FIELDS:
                if name == "config_source":
                    continue
                if row[name]:
                    merged[name] = row[name]
        return merged or None
