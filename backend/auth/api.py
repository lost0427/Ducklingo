from dataclasses import dataclass
from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .store import AuthStore


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class PasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


@dataclass(frozen=True)
class AuthConfig:
    idle_minutes: int = 120
    max_days: int = 14
    failure_limit: int = 5
    lock_minutes: int = 15
    csrf_enabled: bool = True
    cookie_secure: bool = True


def router(store: AuthStore, config: AuthConfig) -> APIRouter:
    api = APIRouter(prefix="/api/auth")

    def current(session: str | None):
        user = store.session_user(session or "", config.idle_minutes)
        if not user:
            raise HTTPException(401, "Authentication required")
        return user

    def check_csrf(request: Request, session: str | None, csrf: str | None, origin: str | None):
        if config.csrf_enabled and (not session or not csrf or not store.csrf_valid(session, csrf) or (origin and origin != str(request.base_url).rstrip("/"))):
            raise HTTPException(403, "CSRF validation failed")

    def check_origin(request: Request, origin: str | None):
        if config.csrf_enabled and origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Origin validation failed")

    @api.post("/login")
    def login(request: Request, data: LoginInput, response: Response, origin: str | None = Header(default=None)):
        check_origin(request, origin)
        user = store.authenticate(data.username, data.password, config.failure_limit, config.lock_minutes, request.client.host if request.client else "unknown")
        if not user:
            raise HTTPException(401, "Invalid account or password")
        session, csrf = store.create_session(user["id"], config.idle_minutes, config.max_days)
        response.set_cookie("ducklingo_session", session, httponly=True, secure=config.cookie_secure, samesite="lax", max_age=config.max_days * 86400)
        response.set_cookie("ducklingo_csrf", csrf, secure=config.cookie_secure, samesite="lax", max_age=config.max_days * 86400)
        return {"id": user["id"], "username": user["username"], "role": user["role"]}

    @api.get("/me")
    def me(ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        return {"id": user["id"], "username": user["username"], "role": user["role"]}

    @api.post("/logout")
    def logout(request: Request, response: Response, origin: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None), ducklingo_session: str | None = Cookie(default=None)):
        current(ducklingo_session)
        check_csrf(request, ducklingo_session, x_csrf_token, origin)
        store.revoke(ducklingo_session)
        response.delete_cookie("ducklingo_session")
        response.delete_cookie("ducklingo_csrf")
        return {"ok": True}

    @api.post("/password")
    def password(request: Request, data: PasswordInput, origin: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None), ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, x_csrf_token, origin)
        if not store.authenticate(user["username"], data.current_password, config.failure_limit, config.lock_minutes):
            raise HTTPException(400, "Current password is incorrect")
        store.change_password(user["id"], data.new_password, ducklingo_session)
        return {"ok": True}

    return api
