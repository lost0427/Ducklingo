import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
WHITESPACE_RUNS = re.compile(r"\s+")
DOT_RUNS = re.compile(r"\.{2,}")
UNDERSCORE_RUNS = re.compile(r"_{2,}")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
MAX_NAME_LENGTH = 120


def sanitize_filename(name: str) -> str:
    value = UNSAFE_CHARS.sub("", WHITESPACE_RUNS.sub("_", name.strip()))
    value = DOT_RUNS.sub(".", UNDERSCORE_RUNS.sub("_", value)).strip("._")
    if not value:
        return "media"
    if value.split(".", 1)[0].lower() in WINDOWS_RESERVED:
        value = f"_{value}"
        if len(value) <= MAX_NAME_LENGTH:
            return value
    elif len(value) <= MAX_NAME_LENGTH:
        return value
    dot = value.rfind(".")
    suffix = value[dot:] if 0 < dot and len(value) - dot <= 6 else ""
    return value[: MAX_NAME_LENGTH - len(suffix)] + suffix


FILE_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


class MediaStore:
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
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    content_type TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS files_user_created ON files(user_id, created_at);
            """)

    def register(self, file_id: str, user_id: int, filename: str, kind: str, size: int, content_type: str = "") -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
                (file_id, user_id, filename, kind, size, content_type, stamp),
            )

    def get(self, file_id: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()

    def list_for_user(self, user_id: int) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute(
                "SELECT * FROM files WHERE user_id = ? ORDER BY created_at DESC, rowid DESC",
                (user_id,),
            ).fetchall()

    def delete(self, file_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def reconcile_user(self, user_id: int, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        known = {row["id"] for row in self.list_for_user(user_id)}
        seen: set[str] = set()
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            if path.name.endswith(".part"):
                path.unlink(missing_ok=True)
                continue
            file_id = path.stem
            if not FILE_ID_PATTERN.fullmatch(file_id):
                continue
            seen.add(file_id)
            if file_id not in known:
                self.register(file_id, user_id, path.name, "upload", path.stat().st_size)
        for row in self.list_for_user(user_id):
            if row["id"] not in seen:
                self.delete(row["id"])
