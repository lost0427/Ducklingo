from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .store import AuthStore


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class PasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


class CreateUserInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=512)
    role: Literal["user", "admin"]


class UserPatch(BaseModel):
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=512)


@dataclass(frozen=True)
class AuthConfig:
    idle_minutes: int = 120
    max_days: int = 14
    failure_limit: int = 5
    lock_minutes: int = 15
    csrf_enabled: bool = True
    cookie_secure: bool = True
    allowed_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    @staticmethod
    def from_mapping(auth: dict) -> "AuthConfig":
        origins = auth.get("allowed_origins")
        return AuthConfig(
            idle_minutes=int(auth.get("session_idle_minutes", 120)),
            max_days=int(auth.get("session_max_days", 14)),
            failure_limit=int(auth.get("login_failure_limit", 5)),
            lock_minutes=int(auth.get("login_lock_minutes", 15)),
            csrf_enabled=bool(auth.get("csrf_enabled", True)),
            cookie_secure=bool(auth.get("cookie_secure", True)),
            allowed_origins=tuple(origins) if origins else ("http://localhost:5173", "http://127.0.0.1:5173"),
        )


def public_user(user) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "is_active": bool(user["is_active"]),
        "created_at": user["created_at"],
        "last_login_at": user["last_login_at"],
    }


def guards(store: AuthStore, config: AuthConfig):
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

    def check_origin(request: Request, origin: str | None):
        if config.csrf_enabled and not origin_allowed(request, origin):
            raise HTTPException(403, "Origin validation failed")

    return current, check_csrf, check_origin


def router(store: AuthStore, config: AuthConfig) -> APIRouter:
    api = APIRouter(prefix="/api/auth")
    current, check_csrf, check_origin = guards(store, config)

    def require_admin(user):
        if user["role"] != "admin":
            raise HTTPException(403, "Admin required")
        return user

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

    @api.get("/users")
    def users(ducklingo_session: str | None = Cookie(default=None)):
        require_admin(current(ducklingo_session))
        return [public_user(user) for user in store.list_users()]

    @api.post("/users")
    def create_user(request: Request, data: CreateUserInput, origin: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None), ducklingo_session: str | None = Cookie(default=None)):
        require_admin(current(ducklingo_session))
        check_csrf(request, ducklingo_session, x_csrf_token, origin)
        try:
            user_id = store.create_user(data.username, data.password, data.role)
        except ValueError as exc:
            if str(exc) == "username exists":
                raise HTTPException(409, "Username already exists") from exc
            raise HTTPException(400, str(exc)) from exc
        return public_user(store.user_by_id(user_id))

    @api.patch("/users/{user_id}")
    def patch_user(request: Request, user_id: int, data: UserPatch, origin: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None), ducklingo_session: str | None = Cookie(default=None)):
        require_admin(current(ducklingo_session))
        check_csrf(request, ducklingo_session, x_csrf_token, origin)
        if not store.user_by_id(user_id):
            raise HTTPException(404, "User not found")
        try:
            user = store.update_user(user_id, is_active=data.is_active, password=data.password)
        except ValueError as exc:
            if str(exc) == "last admin":
                raise HTTPException(400, "Cannot deactivate the last admin") from exc
            raise HTTPException(400, str(exc)) from exc
        return public_user(user)

    return api
