from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth.api import AuthConfig, router
from .auth.store import AuthStore
from .config import load_config
from .media_store import MediaStore
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
    app.include_router(router(store, auth_config))
    app.include_router(video_router(store, auth_config, root, media))

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
