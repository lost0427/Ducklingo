from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .asr_api import router as asr_router
from .auth.api import AuthConfig, router
from .auth.store import AuthStore
from .config import load_config
from .jobs import JobRunner, JobStore, load_job_config
from .media_store import MediaStore
from .settings_store import SettingsStore
from .video_api import router as video_router

ROOT = Path(__file__).resolve().parents[1]


def load_auth_config() -> AuthConfig:
    path = ROOT / "config" / "config.toml"
    if not path.exists():
        return AuthConfig(cookie_secure=False)
    return AuthConfig.from_mapping(load_config(path).get("auth", {}))


def create_app(db_path: str | Path = "data/ducklingo.db", config: AuthConfig | None = None, root: Path | None = None) -> FastAPI:
    root = root or ROOT
    app = FastAPI(title="Ducklingo")
    auth_config = config or load_auth_config()
    store = AuthStore(db_path)
    media = MediaStore(db_path)
    settings = SettingsStore(db_path)
    jobs = JobStore(db_path)
    concurrency = load_job_config(root)
    runner = JobRunner(jobs, cloud_concurrency=concurrency["cloud"], local_concurrency=concurrency["local"])
    runner.fail_stale()
    app.include_router(router(store, auth_config))
    app.include_router(video_router(store, auth_config, root, media))
    app.include_router(asr_router(store, auth_config, root, media, settings, runner))

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    dist = root / "frontend" / "dist"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    if dist.is_dir():
        @app.get("/{path:path}")
        def spa(path: str):
            target = dist / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
