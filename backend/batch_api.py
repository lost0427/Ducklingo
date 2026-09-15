from fastapi import APIRouter, Cookie, HTTPException, Request

from .auth.api import guards
from .batch_store import OPTION_FIELDS, BatchStore
from .batch_scheduler import PipelineScheduler
from .media_store import MediaStore
from .settings_store import SettingsStore


def item_view(row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "file_id": row["file_id"],
        "title": row["title"],
        "status": row["status"],
        "error": row["error"],
    }


def router(store, config, root, media: MediaStore, settings: SettingsStore, batch: BatchStore, scheduler: PipelineScheduler) -> APIRouter:
    api = APIRouter(prefix="/api/batch")
    current, check_csrf, _ = guards(store, config)

    def own(user_id: int, item_id: int):
        row = batch.get(item_id)
        if not row or row["user_id"] != user_id:
            raise HTTPException(404, "Batch item not found")
        return row

    @api.get("")
    def list_items(ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        return {
            "items": [item_view(row) for row in batch.list_for_user(user["id"])],
            "options": batch.options(user["id"]),
        }

    @api.post("")
    async def add(request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        body = await request.json()
        urls = [
            url
            for url in (str(value).strip() for value in body.get("urls") or [])
            if url.startswith(("http://", "https://"))
        ]
        if not urls:
            raise HTTPException(400, "no valid urls")
        ids = batch.add(user["id"], urls)
        for item_id in ids:
            scheduler.submit(item_id)
        return {"ids": ids}

    @api.put("/options")
    async def put_options(request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        body = await request.json()
        batch.set_options(user["id"], {name: value for name, value in body.items() if name in OPTION_FIELDS})
        return {"ok": True}

    @api.post("/{item_id}/retry")
    async def retry(item_id: int, request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        own(user["id"], item_id)
        scheduler.retry(item_id)
        return {"ok": True}

    @api.post("/{item_id}/cancel")
    async def cancel(item_id: int, request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        own(user["id"], item_id)
        scheduler.cancel(item_id)
        return {"ok": True}

    @api.delete("/{item_id}")
    async def remove(item_id: int, request: Request, ducklingo_session: str | None = Cookie(default=None)):
        user = current(ducklingo_session)
        check_csrf(request, ducklingo_session, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        own(user["id"], item_id)
        scheduler.cancel(item_id)
        batch.delete(item_id)
        return {"ok": True}

    return api
