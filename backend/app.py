from pathlib import Path

from fastapi import FastAPI

from .auth.api import AuthConfig, router
from .auth.store import AuthStore


def create_app(db_path: str | Path = "data/ducklingo.db", config: AuthConfig | None = None) -> FastAPI:
    app = FastAPI(title="Ducklingo")
    app.include_router(router(AuthStore(db_path), config or AuthConfig(cookie_secure=False)))

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    return app


app = create_app()
