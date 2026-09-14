import asyncio
from pathlib import Path
from uuid import uuid4

from .asr import checkpoint_words, load_checkpoint
from .batch_store import BatchStore
from .logging_config import get_logger
from .media_store import MediaStore, sanitize_filename
from .pipeline import load_section, load_sentences, run_asr, run_sentence, run_translate
from .settings_store import SettingsStore
from .video_api import media_title
from .workflow import download_from_config

log = get_logger("batch")
TERMINAL = {"done", "error", "cancelled"}


class PipelineScheduler:
    def __init__(self, batch: BatchStore, media: MediaStore, settings: SettingsStore, root: Path, slots: dict):
        self.batch = batch
        self.media = media
        self.settings = settings
        self.root = Path(root)
        self.slots = slots
        self.tasks: dict[int, asyncio.Task] = {}

    async def start(self) -> None:
        self.batch.resume_inflight()
        for item in self.batch.active():
            self._spawn(item["id"])

    async def stop(self) -> None:
        for task in list(self.tasks.values()):
            task.cancel()

    def submit(self, item_id: int) -> None:
        self._spawn(item_id)

    async def retry(self, item_id: int) -> None:
        self.batch.update(item_id, status="pending", error="")
        self._spawn(item_id)

    def cancel(self, item_id: int) -> bool:
        task = self.tasks.get(item_id)
        if task and not task.done():
            task.cancel()
            return True
        self.batch.update(item_id, status="cancelled")
        return False

    def _spawn(self, item_id: int) -> None:
        existing = self.tasks.get(item_id)
        if existing and not existing.done():
            return
        self.tasks[item_id] = asyncio.create_task(self._run(item_id))

    def _media_dir(self, user_id: int) -> Path:
        return self.root / "data" / "uploads" / str(user_id)

    def _source(self, user_id: int, file_id: str) -> Path | None:
        if not file_id:
            return None
        row = self.media.get(file_id)
        if not row or row["user_id"] != user_id:
            return None
        path = self._media_dir(user_id) / f"{file_id}{Path(row['filename']).suffix.lower()}"
        return path if path.is_file() else None

    def _archive(self, directory: Path, file_id: str) -> bool:
        state = load_checkpoint(directory / f"{file_id}.translation.json") or {}
        name = Path(state.get("archive", "")).name
        return bool(name) and (directory / name).is_file()

    def next_stage(self, item) -> str | None:
        user_id = item["user_id"]
        file_id = item["file_id"]
        directory = self._media_dir(user_id)
        if not self._source(user_id, file_id):
            return "download"
        if not (directory / f"{file_id}.asr.json").exists():
            return "asr"
        if not (directory / f"{file_id}.sentences.json").exists():
            return "sentence"
        if not self._archive(directory, file_id):
            return "translate"
        return None

    def _pool(self, stage: str, user_id: int) -> str:
        if stage == "download":
            return "download"
        if stage in ("sentence", "translate"):
            return "llm"
        kind = (self.settings.resolve(user_id, "asr") or {}).get("kind")
        if kind not in ("cloud", "local"):
            raise RuntimeError("ASR service not configured")
        return kind

    async def _run(self, item_id: int) -> None:
        try:
            while True:
                item = self.batch.get(item_id)
                if item is None or item["status"] in TERMINAL:
                    return
                stage = self.next_stage(item)
                if stage is None:
                    self.batch.update(item_id, status="done", error="")
                    return
                self.batch.update(item_id, status=stage, error="")
                async with self.slots[self._pool(stage, item["user_id"])]:
                    await self._run_stage(stage, item)
        except asyncio.CancelledError:
            self.batch.update(item_id, status="cancelled")
            raise
        except Exception as exc:
            log.exception("batch item failed item_id=%s", item_id)
            self.batch.update(item_id, status="error", error=(str(exc) or type(exc).__name__)[:500])
        finally:
            self.tasks.pop(item_id, None)

    async def _run_stage(self, stage: str, item) -> None:
        user_id = item["user_id"]
        file_id = item["file_id"]
        directory = self._media_dir(user_id)
        if stage == "download":
            return await self._download(item)
        source = self._source(user_id, file_id)
        if source is None:
            raise RuntimeError("media file is missing")
        options = self.batch.options(user_id)
        if stage == "asr":
            settings = self.settings.resolve(user_id, "asr")
            if not settings or settings.get("kind") not in ("cloud", "local"):
                raise RuntimeError("ASR service not configured")
            if settings["kind"] == "local" and options["language"]:
                settings = {**settings, "language": options["language"]}
            await run_asr(lambda _: None, self.root, source, directory, file_id, settings, load_section(self.root, "asr"))
        elif stage == "sentence":
            settings = self.settings.resolve(user_id, "sentence")
            if not settings:
                raise RuntimeError("Sentence LLM not configured")
            state = load_checkpoint(directory / f"{file_id}.asr.json") or {}
            timeout = float(load_section(self.root, "asr").get("timeout_seconds", 300))
            await run_sentence(lambda _: None, directory, file_id, checkpoint_words(state), settings, load_section(self.root, "sentence"), timeout)
        else:
            settings = self.settings.resolve(user_id, "translate")
            if not settings or not all(settings.get(name) for name in ("base_url", "model", "api_key")):
                raise RuntimeError("Translation service not configured")
            section = {**load_section(self.root, "translate"), "context_window_tokens": int(options["context_window_tokens"]), "max_context_ratio": float(options["max_context_ratio"])}
            await run_translate(
                lambda _: None,
                self.root,
                source,
                directory,
                file_id,
                user_id,
                load_sentences(directory, file_id),
                settings,
                section,
                options["target_language"],
                options["mode"],
                options["reasoning_effort"],
                float(section.get("timeout_seconds", 300)),
            )

    async def _download(self, item) -> None:
        user_id = item["user_id"]
        directory = self._media_dir(user_id)
        directory.mkdir(parents=True, exist_ok=True)
        file_id = uuid4().hex
        output = directory / f"{file_id}.mp4"
        try:
            await asyncio.to_thread(
                download_from_config, item["url"], output, "config/config.toml", self.root, self.batch.options(user_id)["quality"]
            )
            title = media_title(output)
            name = sanitize_filename(title) + ".mp4" if title else sanitize_filename(f"ducklingo-{file_id}.mp4")
            self.media.register(file_id, user_id, name, "download", output.stat().st_size, "video/mp4", item["url"])
        except Exception:
            for path in directory.glob(f"{file_id}*"):
                path.unlink(missing_ok=True)
            raise
        self.batch.update(item["id"], file_id=file_id)
