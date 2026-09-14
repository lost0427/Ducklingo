import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import FileResponse

from .asr import kill_local, probe_duration, save_checkpoint, transcribe_cloud, transcribe_local, validate_timeline
from .auth.api import guards
from .config import load_config, project_path
from .media_store import FILE_ID_PATTERN
from .models import download_model, list_models, load_registry, snapshot_status
from .sentence import split_sentences
from .settings_store import FIELDS, GLOBAL, SERVICES, SettingsStore
from .tools import ensure_ffmpeg
from .jobs import JobRunner
from .translation import create_archive, translate_sentences

ARTIFACT_PATTERNS = ("{fid}.asr*", "{fid}.srt", "{fid}.sentences.json", "{fid}.translation.json", "{fid}.words.json")


def load_section(root: Path, name: str) -> dict:
    path = Path(root) / "config" / "config.toml"
    return load_config(path).get(name, {}) if path.exists() else {}


def resolve_ffmpeg(root: Path) -> Path:
    tools = load_section(root, "tools")
    return ensure_ffmpeg(
        project_path(tools.get("data_dir", "data/tools"), root),
        tools.get("ffmpeg_release_tag", "autobuild-2026-08-25-13-06"),
        tools.get("ffmpeg_variant", "gpl"),
    )


def masked(row) -> dict | None:
    if row is None:
        return None
    data = {name: row[name] for name in FIELDS}
    key = data.pop("api_key")
    data["has_key"] = bool(key)
    data["api_key_masked"] = f"{key[:3]}…{key[-4:]}" if len(key) > 8 else ("…" if key else "")
    return data


def router(
    store,
    config,
    root: Path,
    media,
    settings: SettingsStore,
    runner: JobRunner,
) -> APIRouter:
    api = APIRouter(prefix="/api/asr")
    current, check_csrf, _ = guards(store, config)
    downloads: set[asyncio.Task] = set()

    def media_dir(user_id: int) -> Path:
        return Path(root) / "data" / "uploads" / str(user_id)

    def own_file(user_id: int, file_id: str):
        if not FILE_ID_PATTERN.fullmatch(file_id):
            raise HTTPException(404, "File not found")
        row = media.get(file_id)
        if not row or row["user_id"] != user_id:
            raise HTTPException(404, "File not found")
        return row

    def source_path(user_id: int, row) -> Path:
        extension = Path(row["filename"]).suffix.lower()
        path = media_dir(user_id) / f"{row['id']}{extension}"
        if not path.is_file():
            raise HTTPException(404, "File not found")
        return path

    @api.get("/settings")
    def get_settings(ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)

        def view(service: str) -> dict:
            shared = settings.get(GLOBAL, service)
            return {
                "own": masked(settings.get(user["id"], service)),
                "shared": masked(shared) if user["role"] == "admin" else None,
                "has_shared": shared is not None and any(shared[name] for name in FIELDS),
            }

        return {service: view(service) for service in SERVICES}

    @api.put("/settings")
    async def put_settings(request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        body = await request.json()
        service = body.get("service")
        if service not in SERVICES:
            raise HTTPException(400, "unknown service")
        scope = body.get("scope", "user")
        if scope == "shared":
            if user["role"] != "admin":
                raise HTTPException(403, "admin only")
            target = GLOBAL
        elif scope == "user":
            target = user["id"]
        else:
            raise HTTPException(400, "unknown scope")
        values = dict(body.get("values") or {})
        if values.get("api_key") is None:
            values.pop("api_key", None)
        existing = settings.get(target, service)
        merged = {name: existing[name] for name in FIELDS} if existing else {}
        try:
            settings.put(target, service, {**merged, **values})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @api.get("/models")
    def get_models(ducklingo_session: str | None = Cookie(default=None)):
        current(ducklingo_session)
        status = snapshot_status()
        return [{**model, "download": status.get(model["id"])} for model in list_models(root)]

    @api.post("/models/download")
    async def post_download(request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        if user["role"] != "admin":
            raise HTTPException(403, "admin only")
        body = await request.json()
        model_id = str(body.get("id") or "")
        if not any(entry["id"] == model_id for entry in load_registry(root)):
            raise HTTPException(400, "unknown model")
        status = snapshot_status().get(model_id)
        if status and status.get("status") == "downloading":
            raise HTTPException(409, "download already running")

        async def job():
            try:
                await asyncio.to_thread(download_model, root, model_id)
            except Exception:
                pass

        task = asyncio.create_task(job())
        downloads.add(task)
        task.add_done_callback(downloads.discard)
        return {"ok": True}

    @api.post("/start")
    async def start(request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        body = await request.json()
        file_id = str(body.get("file_id") or "")
        row = own_file(user["id"], file_id)
        if runner.active(file_id, "asr"):
            raise HTTPException(409, "job already running")
        asr_settings = settings.resolve(user["id"], "asr")
        if not asr_settings or asr_settings.get("kind") not in ("cloud", "local"):
            raise HTTPException(400, "ASR service not configured")
        sentence_settings = settings.resolve(user["id"], "sentence")
        if not sentence_settings:
            raise HTTPException(400, "Sentence LLM not configured")
        explicit_target = "target_language" in body
        target_language = str(body.get("target_language") or "").strip()
        translate_settings = settings.resolve(user["id"], "translate")
        has_target = explicit_target or translate_settings is not None
        if explicit_target and not target_language:
            raise HTTPException(400, "target_language is required")
        if has_target and not target_language:
            target_language = "简体中文"
        if "reflect" in body and not isinstance(body["reflect"], bool):
            raise HTTPException(400, "reflect must be a boolean")
        if has_target and (not translate_settings or not all(translate_settings.get(name) for name in ("base_url", "model", "api_key"))):
            raise HTTPException(400, "Translation service not configured")
        mode = "reflect" if body.get("reflect") else str(body.get("mode") or "standard")
        if mode not in ("standard", "reflect"):
            raise HTTPException(400, "translation mode must be standard or reflect")
        reasoning_effort = str(body.get("reasoning_effort") or "off")
        if reasoning_effort not in ("off", "low", "medium", "high"):
            raise HTTPException(400, "invalid reasoning_effort")
        asr_section = load_section(root, "asr")
        sentence_section = load_section(root, "sentence")
        translate_section = load_section(root, "translate")
        if has_target:
            try:
                if "context_window_tokens" in body:
                    translate_section["context_window_tokens"] = int(body["context_window_tokens"])
                if "max_context_ratio" in body:
                    translate_section["max_context_ratio"] = float(body["max_context_ratio"])
                if translate_section.get("context_window_tokens", 131072) < 1:
                    raise ValueError("context window must be positive")
                ratio = float(translate_section.get("max_context_ratio", 0.8))
                if not 0.1 <= ratio <= 0.95:
                    raise ValueError("context ratio must be between 0.1 and 0.95")
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from exc
        language = str(body.get("language") or "").strip()
        if asr_settings["kind"] == "local" and language:
            asr_settings = {**asr_settings, "language": language}
        source = source_path(user["id"], row)
        directory = media_dir(user["id"])

        async def work(report):
            ffmpeg = await asyncio.to_thread(resolve_ffmpeg, root)
            if asr_settings["kind"] == "cloud":
                words = await transcribe_cloud(report, source, directory, file_id, asr_settings, asr_section, ffmpeg)
            else:
                words = await transcribe_local(report, source, directory, file_id, asr_settings, asr_section, ffmpeg, root)
            sentences = await split_sentences(
                report,
                words,
                sentence_settings,
                sentence_section,
                directory / f"{file_id}.sentences.json",
                float(asr_section.get("timeout_seconds", 300)),
            )
            duration = await asyncio.to_thread(probe_duration, source, ffmpeg)
            sentences = validate_timeline(sentences, duration)
            if has_target:
                translated = await translate_sentences(
                    report,
                    sentences,
                    translate_settings,
                    translate_section,
                    directory / f"{file_id}.translation.json",
                    target_language=target_language,
                    mode=mode,
                    reasoning_effort=reasoning_effort,
                    cache_dir=Path(root) / "data" / "translation-cache" / str(user["id"]),
                    timeout=float(translate_section.get("timeout_seconds", asr_section.get("timeout_seconds", 300))),
                )
                archive = await asyncio.to_thread(create_archive, directory, translated)
                state = json.loads((directory / f"{file_id}.translation.json").read_text("utf-8"))
                state["archive"] = archive.name
                save_checkpoint(directory / f"{file_id}.translation.json", state)

        runner.enqueue(file_id, "asr", user["id"], asr_settings["kind"], work, username=user["username"])
        return {"ok": True}

    @api.get("/status/{file_id}")
    def get_status(file_id: str, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        own_file(user["id"], file_id)
        job = runner.store.get(file_id, "asr")
        translation = load_translation_checkpoint(media_dir(user["id"]), file_id)
        return {
            "status": job["status"] if job else "idle",
            "stage": job["stage"] if job else "",
            "error": job["error"] if job else "",
            "completed_batches": len(translation.get("translations", {})) if translation else 0,
            "total_batches": int(translation.get("total_items", 0)) if translation else 0,
            "target_language": translation.get("target_language", "") if translation else "",
            "mode": translation.get("mode", "standard") if translation else "standard",
            "reasoning_effort": translation.get("reasoning_effort", "off") if translation else "off",
            "context_window_tokens": translation.get("context_window_tokens", 131072) if translation else 131072,
            "max_context_ratio": translation.get("max_context_ratio", 0.8) if translation else 0.8,
            "has_archive": bool(translation and translation.get("archive") and (media_dir(user["id"]) / Path(translation["archive"]).name).is_file()),
        }

    @api.post("/cancel/{file_id}")
    def cancel(file_id: str, request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        own_file(user["id"], file_id)
        if not runner.cancel(file_id, "asr"):
            job = runner.store.get(file_id, "asr")
            if job and job["status"] in ("queued", "running", "cancelling"):
                runner.store.update(file_id, "asr", status="cancelled")
        return {"ok": True}

    @api.post("/reset/{file_id}")
    async def reset(file_id: str, request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        own_file(user["id"], file_id)
        runner.cancel(file_id, "asr")
        kill_local(file_id)
        directory = media_dir(user["id"])
        translation = load_translation_checkpoint(directory, file_id)
        if translation and translation.get("archive"):
            (directory / Path(translation["archive"]).name).unlink(missing_ok=True)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if body.get("scope") == "translation":
            (directory / f"{file_id}.translation.json").unlink(missing_ok=True)
            runner.store.delete(file_id, "asr")
            return {"ok": True}
        for pattern in ARTIFACT_PATTERNS:
            for path in directory.glob(pattern.format(fid=file_id)):
                path.unlink(missing_ok=True)
        runner.store.delete(file_id, "asr")
        return {"ok": True}

    @api.get("/archive/{file_id}")
    def get_archive(file_id: str, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        row = own_file(user["id"], file_id)
        directory = media_dir(user["id"])
        translation = load_translation_checkpoint(directory, file_id)
        archive_name = Path(translation.get("archive", "")).name if translation else ""
        path = directory / archive_name if archive_name else None
        if not path or not path.is_file():
            raise HTTPException(404, "Subtitle archive not found")
        return FileResponse(path, media_type="application/zip", filename=path.name)

    return api


def load_translation_checkpoint(directory: Path, file_id: str) -> dict | None:
    try:
        value = json.loads((directory / f"{file_id}.translation.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
