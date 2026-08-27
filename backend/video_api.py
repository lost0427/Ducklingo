from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, BaseModel

from .auth.api import AuthConfig
from .auth.store import AuthStore
from .config import load_config
from .media_store import FILE_ID_PATTERN, MediaStore, sanitize_filename
from .video import VideoQuality
from .workflow import download_from_config


DEFAULT_UPLOAD_EXTENSIONS = ("mp4", "mov", "avi", "mkv", "webm", "m4v", "wav", "mp3", "flac", "m4a")


def load_upload_config(root: Path) -> dict[str, int | tuple[str, ...]]:
    path = root / "config" / "config.toml"
    config = load_config(path) if path.exists() else {}
    upload = config.get("upload", {})
    size_gb = float(upload.get("max_file_size_gb", 2))
    if size_gb <= 0:
        raise ValueError("upload.max_file_size_gb must be positive")
    extensions = tuple(
        str(extension).lower().lstrip(".")
        for extension in upload.get("allowed_extensions", DEFAULT_UPLOAD_EXTENSIONS)
    )
    return {
        "max_bytes": int(size_gb * 1024**3),
        "extensions": extensions or DEFAULT_UPLOAD_EXTENSIONS,
    }


class VideoDownloadInput(BaseModel):
    url: AnyHttpUrl
    quality: VideoQuality = "1080"


def router(store: AuthStore, config: AuthConfig, root: Path, media: MediaStore | None = None) -> APIRouter:
    api = APIRouter(prefix="/api/video")
    upload = load_upload_config(root)
    media = media or MediaStore(root / "data" / "ducklingo.db")

    def media_dir(user_id: int) -> Path:
        path = root / "data" / "uploads" / str(user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    uploads_root = root / "data" / "uploads"
    if uploads_root.exists():
        for child in sorted(uploads_root.iterdir()):
            if child.is_dir() and child.name.isdigit():
                media.reconcile_user(int(child.name), child)

    def current(session: str | None):
        user = store.session_user(session or "", config.idle_minutes)
        if not user:
            raise HTTPException(401, "Authentication required")
        return user

    def origin_allowed(request: Request, origin: str | None) -> bool:
        if not origin:
            return True
        allowed = {str(request.base_url).rstrip("/"), *config.allowed_origins}
        return origin in allowed

    def check_csrf(request: Request, session: str | None, csrf: str | None, origin: str | None):
        if config.csrf_enabled and (not session or not csrf or not store.csrf_valid(session, csrf) or not origin_allowed(request, origin)):
            raise HTTPException(403, "CSRF validation failed")

    @api.post("/download")
    def download(
        request: Request,
        data: VideoDownloadInput,
        origin: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
        ducklingo_session: str | None = Cookie(default=None),
    ):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, x_csrf_token, origin)

        file_id = uuid4().hex
        output = media_dir(user["id"]) / f"{file_id}.mp4"
        try:
            download_from_config(str(data.url), output, root=root, quality=data.quality)
        except (OSError, RuntimeError) as exc:
            output.unlink(missing_ok=True)
            raise HTTPException(502, str(exc)[:1000] or "video download failed") from exc

        filename = sanitize_filename(f"ducklingo-{datetime.now():%Y%m%d-%H%M%S}.mp4")
        media.register(file_id, user["id"], filename, "download", output.stat().st_size, "video/mp4")
        return {"id": file_id, "filename": filename, "size": output.stat().st_size}

    @api.get("/files")
    def list_files(ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        return [
            {
                "id": row["id"],
                "filename": row["filename"],
                "kind": row["kind"],
                "size": row["size"],
                "contentType": row["content_type"],
                "createdAt": row["created_at"],
            }
            for row in media.list_for_user(user["id"])
        ]

    @api.get("/file/{file_id}")
    def get_file(file_id: str, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        if not FILE_ID_PATTERN.fullmatch(file_id):
            raise HTTPException(404, "File not found")
        row = media.get(file_id)
        if not row or row["user_id"] != user["id"]:
            raise HTTPException(404, "File not found")
        matches = list(media_dir(user["id"]).glob(f"{file_id}.*"))
        if not matches:
            raise HTTPException(404, "File not found")
        return FileResponse(matches[0], media_type=row["content_type"] or "application/octet-stream", filename=row["filename"])

    @api.get("/upload-config")
    def upload_config(ducklingo_session: str | None = Cookie(default=None)):
        current(ducklingo_session)
        extensions = upload["extensions"]
        return {
            "maxBytes": upload["max_bytes"],
            "extensions": extensions,
            "accept": ",".join("." + extension for extension in extensions),
        }

    @api.post("/upload")
    async def upload_file(
        request: Request,
        filename: str,
        origin: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
        ducklingo_session: str | None = Cookie(default=None),
    ):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, x_csrf_token, origin)
        safe_name = Path(filename.replace("\\", "/")).name
        extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        if not safe_name or not extension or extension not in upload["extensions"]:
            raise HTTPException(415, "Unsupported file format")

        max_bytes = upload["max_bytes"]
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_bytes:
            raise HTTPException(413, "File exceeds the configured size limit")

        directory = media_dir(user["id"])
        file_id = uuid4().hex
        temporary = directory / f"{file_id}.part"
        target = directory / f"{file_id}.{extension}"
        total = 0
        try:
            with temporary.open("wb") as output:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(413, "File exceeds the configured size limit")
                    output.write(chunk)
            temporary.replace(target)
        except HTTPException:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise HTTPException(507, "Could not save uploaded file") from exc
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

        display_name = sanitize_filename(safe_name)
        content_type = request.headers.get("content-type", "application/octet-stream")
        media.register(file_id, user["id"], display_name, "upload", total, content_type)

        return {
            "id": file_id,
            "filename": display_name,
            "size": total,
            "contentType": content_type,
        }

    @api.delete("/upload/{file_id}")
    def delete_upload(
        file_id: str,
        request: Request,
        origin: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
        ducklingo_session: str | None = Cookie(default=None),
    ):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, x_csrf_token, origin)
        if not FILE_ID_PATTERN.fullmatch(file_id):
            raise HTTPException(404, "Uploaded file not found")
        directory = media_dir(user["id"])
        for path in directory.glob(f"{file_id}.*"):
            path.unlink(missing_ok=True)
        media.delete(file_id)
        return {"ok": True}

    return api
