import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ACTIVE_STATUSES = ("download", "asr", "sentence", "translate")
DEFAULT_OPTIONS = {
    "quality": "1080",
    "language": "",
    "target_language": "简体中文",
    "mode": "standard",
    "reasoning_effort": "off",
    "context_window_tokens": "131072",
    "max_context_ratio": "0.8",
}
OPTION_FIELDS = tuple(DEFAULT_OPTIONS)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class BatchStore:
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
                CREATE TABLE IF NOT EXISTS batch_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    file_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS batch_items_user ON batch_items(user_id, id);
                CREATE TABLE IF NOT EXISTS batch_options (
                    user_id INTEGER PRIMARY KEY,
                    quality TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT '',
                    target_language TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    reasoning_effort TEXT NOT NULL DEFAULT '',
                    context_window_tokens TEXT NOT NULL DEFAULT '',
                    max_context_ratio TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
            """)

    def add(self, user_id: int, urls: list[str]) -> list[int]:
        stamp = _stamp()
        with self._db() as db:
            ids = []
            for url in urls:
                cursor = db.execute(
                    "INSERT INTO batch_items(user_id, url, status, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)",
                    (user_id, url, stamp, stamp),
                )
                ids.append(cursor.lastrowid)
            return ids

    def list_for_user(self, user_id: int) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute(
                "SELECT * FROM batch_items WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()

    def get(self, item_id: int) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM batch_items WHERE id = ?", (item_id,)).fetchone()

    def update(self, item_id: int, **fields) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._db() as db:
            db.execute(
                f"UPDATE batch_items SET {assignments}, updated_at = ? WHERE id = ?",
                (*fields.values(), _stamp(), item_id),
            )

    def delete(self, item_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM batch_items WHERE id = ?", (item_id,))

    def resume_inflight(self) -> int:
        with self._db() as db:
            cursor = db.execute(
                f"UPDATE batch_items SET status = 'pending', updated_at = ?"
                f" WHERE status IN ({', '.join('?' for _ in ACTIVE_STATUSES)})",
                (_stamp(), *ACTIVE_STATUSES),
            )
            return cursor.rowcount

    def options(self, user_id: int) -> dict:
        with self._db() as db:
            row = db.execute("SELECT * FROM batch_options WHERE user_id = ?", (user_id,)).fetchone()
        return {**DEFAULT_OPTIONS, **{name: row[name] for name in OPTION_FIELDS}} if row else dict(DEFAULT_OPTIONS)

    def set_options(self, user_id: int, values: dict) -> None:
        merged = {name: str(values.get(name, self.options(user_id)[name])) for name in OPTION_FIELDS}
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO batch_options"
                f" (user_id, {', '.join(OPTION_FIELDS)}, updated_at)"
                f" VALUES (?, {', '.join('?' for _ in OPTION_FIELDS)}, ?)",
                (user_id, *merged.values(), _stamp()),
            )
