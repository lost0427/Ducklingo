import asyncio
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config

PROVIDERS = ("cloud", "local")
ERROR_LIMIT = 1000


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
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
                CREATE TABLE IF NOT EXISTS jobs (
                    file_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (file_id, kind)
                );
            """)

    def enqueue(self, file_id: str, kind: str, user_id: int) -> None:
        stamp = _stamp()
        with self._db() as db:
            db.execute(
                "INSERT INTO jobs(file_id, kind, user_id, status, created_at, updated_at)"
                " VALUES (?, ?, ?, 'queued', ?, ?)"
                " ON CONFLICT(file_id, kind) DO UPDATE SET"
                " status = 'queued', stage = '', error = '',"
                " user_id = excluded.user_id, updated_at = excluded.updated_at",
                (file_id, kind, user_id, stamp, stamp),
            )

    def update(self, file_id: str, kind: str, **fields) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._db() as db:
            db.execute(
                f"UPDATE jobs SET {assignments}, updated_at = ? WHERE file_id = ? AND kind = ?",
                (*fields.values(), _stamp(), file_id, kind),
            )

    def get(self, file_id: str, kind: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute(
                "SELECT * FROM jobs WHERE file_id = ? AND kind = ?",
                (file_id, kind),
            ).fetchone()

    def delete(self, file_id: str, kind: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM jobs WHERE file_id = ? AND kind = ?", (file_id, kind))

    def fail_stale(self) -> int:
        with self._db() as db:
            cursor = db.execute(
                "UPDATE jobs SET status = 'error', error = 'interrupted', updated_at = ?"
                " WHERE status IN ('queued', 'running', 'cancelling')",
                (_stamp(),),
            )
            return cursor.rowcount


class JobRunner:
    def __init__(self, store: JobStore, cloud_concurrency: int = 4, local_concurrency: int = 1):
        self.store = store
        self.slots = {
            "cloud": asyncio.Semaphore(max(1, cloud_concurrency)),
            "local": asyncio.Semaphore(max(1, local_concurrency)),
        }
        self.tasks: dict[tuple[str, str], asyncio.Task] = {}

    def enqueue(self, file_id: str, kind: str, user_id: int, provider: str, work) -> asyncio.Task:
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider: {provider}")
        key = (file_id, kind)
        active = self.tasks.get(key)
        if active and not active.done():
            raise RuntimeError("job already running")
        self.store.enqueue(file_id, kind, user_id)
        task = asyncio.create_task(self._run(key, provider, work))
        self.tasks[key] = task
        return task

    async def _run(self, key: tuple[str, str], provider: str, work) -> None:
        file_id, kind = key

        def report(stage: str) -> None:
            self.store.update(file_id, kind, stage=stage)

        try:
            async with self.slots[provider]:
                self.store.update(file_id, kind, status="running")
                await work(report)
        except asyncio.CancelledError:
            self.store.update(file_id, kind, status="cancelled")
        except Exception as exc:
            self.store.update(file_id, kind, status="error", error=(str(exc) or type(exc).__name__)[:ERROR_LIMIT])
        else:
            self.store.update(file_id, kind, status="done", stage="")
        finally:
            self.tasks.pop(key, None)

    def active(self, file_id: str, kind: str) -> bool:
        task = self.tasks.get((file_id, kind))
        return bool(task and not task.done())

    def cancel(self, file_id: str, kind: str) -> bool:
        task = self.tasks.get((file_id, kind))
        if not task or task.done():
            return False
        self.store.update(file_id, kind, status="cancelling")
        task.cancel()
        return True

    def fail_stale(self) -> int:
        return self.store.fail_stale()


def load_job_config(root: Path) -> dict:
    path = Path(root) / "config" / "config.toml"
    asr = load_config(path).get("asr", {}) if path.exists() else {}
    return {
        "cloud": max(1, int(asr.get("cloud_concurrency", 4))),
        "local": max(1, int(asr.get("local_concurrency", 1))),
    }
