import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Cookie, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, BaseModel

from .auth.api import AuthConfig
from .auth.store import AuthStore
from .config import load_config
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


def router(store: AuthStore, config: AuthConfig, root: Path) -> APIRouter:
    api = APIRouter(prefix="/api/video")
    upload = load_upload_config(root)

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
        background_tasks: BackgroundTasks,
        origin: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
        ducklingo_session: str | None = Cookie(default=None),
    ):
        current(ducklingo_session)
        check_csrf(request, ducklingo_session, x_csrf_token, origin)

        temp_root = root / "data" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="ducklingo-download-", dir=temp_root))
        output = directory / "ducklingo-video.mp4"
        try:
            downloaded = download_from_config(str(data.url), output, root=root, quality=data.quality)
        except (OSError, RuntimeError) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(502, str(exc)[:1000] or "video download failed") from exc

        background_tasks.add_task(shutil.rmtree, directory, ignore_errors=True)
        return FileResponse(downloaded, media_type="video/mp4", filename="ducklingo-video.mp4", background=background_tasks)

    @api.get("/upload-config")
    def upload_config(ducklingo_session: str | None = Cookie(default=None)):
        current(ducklingo_session)
        extensions = upload["extensions"]
        return {
            "maxBytes": upload["max_bytes"],
            "extensions": extensions,
            "accept": ",".join("." + extension for extension in extensions),
        }

    return api
