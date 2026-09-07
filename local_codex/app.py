"""HTTP transport and application factory. Importing this module has no runtime effects."""

from __future__ import annotations
import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any
import httpx
import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from .runtime import Runtime
from .service import execute
from .settings import SETTINGS

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    if not request.app.state.runtime.client:
        return {"status": "starting"}
    try:
        response = await request.app.state.runtime.client.get(
            f"{request.app.state.runtime.settings.ollama_base_url}/api/version", timeout=3.0
        )
        response.raise_for_status()
        return {
            "status": "ok",
            "ollama": response.json().get("version"),
            "active_model": request.app.state.runtime.current_model,
            "pid": os.getpid(),
            "managed": request.app.state.runtime.managed,
            "sessions": request.app.state.runtime.leases.snapshot()["active_count"],
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {"status": "degraded", "error": str(exc)}


@router.get("/v1/models")
async def models(request: Request) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": request.app.state.runtime.settings.public_model,
                "object": "model",
                "created": 0,
                "owned_by": "local",
            }
        ],
    }


def _monitor_payload(request: Request) -> dict[str, Any]:
    snapshot = request.app.state.runtime.telemetry.snapshot()
    snapshot["sessions"] = request.app.state.runtime.leases.snapshot()
    snapshot["router"] = {
        "status": "online",
        "pid": os.getpid(),
        "managed": request.app.state.runtime.managed,
    }
    return snapshot


@router.get("/monitor/snapshot")
async def monitor_snapshot(request: Request) -> dict[str, Any]:
    """Loopback-only compatibility snapshot for the native monitor."""
    return _monitor_payload(request)


@router.get("/monitor/events")
async def monitor_events(request: Request) -> StreamingResponse:
    """Coalesced live dashboard events without database or inference work."""

    async def stream():
        last_revision = -1
        last_heartbeat = 0.0
        last_snapshot = 0.0
        while not await request.is_disconnected():
            now = asyncio.get_running_loop().time()
            revision = request.app.state.runtime.telemetry.revision
            active = request.app.state.runtime.telemetry.active
            if revision != last_revision or (active and now - last_snapshot >= 1.0):
                payload = json.dumps(_monitor_payload(request), ensure_ascii=False, separators=(",", ":"))
                yield f"event: snapshot\ndata: {payload}\n\n"
                last_revision = revision
                last_heartbeat = now
                last_snapshot = now
            elif now - last_heartbeat >= 15.0:
                yield ": heartbeat\n\n"
                last_heartbeat = now
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/monitor/history")
async def monitor_history(request: Request) -> dict[str, Any]:
    # This endpoint is polled slowly by the tray app; live snapshot polling
    # does not touch SQLite.
    return request.app.state.runtime.usage.summary(1)


@router.get("/monitor/statistics")
async def monitor_statistics(
    request: Request,
    period: str = "30d",
    session_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> Any:
    try:
        return request.app.state.runtime.usage.statistics(
            period=period, session_id=session_id, page=page, page_size=page_size
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/monitor/statistics/export")
async def monitor_statistics_export(
    request: Request, period: str = "30d", session_id: str | None = None, format: str = "json"
) -> Response:
    try:
        payload = request.app.state.runtime.usage.export(period=period, session_id=session_id, format=format)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    media_type = "text/csv" if format == "csv" else "application/json"
    filename = f"localcodex-usage-{period}.{format}"
    return Response(
        payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/monitor/leases/{lease_id}")
async def register_monitor_lease(lease_id: str, request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    process_id = body.get("process_id", 0) if isinstance(body, dict) else 0
    lease = request.app.state.runtime.leases.register(lease_id, int(process_id or 0))
    if request.app.state.runtime.shutdown_task and not request.app.state.runtime.shutdown_task.done():
        request.app.state.runtime.shutdown_task.cancel()
    request.app.state.runtime._shutdown_requested = False
    return {"ok": True, "lease_id": lease.lease_id, **request.app.state.runtime.leases.snapshot()}


@router.put("/monitor/leases/{lease_id}")
async def heartbeat_monitor_lease(lease_id: str, request: Request) -> JSONResponse:
    alive = request.app.state.runtime.leases.heartbeat(lease_id)
    return JSONResponse(
        {"ok": alive, **request.app.state.runtime.leases.snapshot()}, status_code=200 if alive else 404
    )


@router.delete("/monitor/leases/{lease_id}")
async def release_monitor_lease(lease_id: str, request: Request) -> dict[str, Any]:
    released = request.app.state.runtime.leases.release(lease_id)
    snapshot = request.app.state.runtime.leases.snapshot()
    if snapshot["active_count"] == 0 and (
        not request.app.state.runtime.shutdown_task or request.app.state.runtime.shutdown_task.done()
    ):
        request.app.state.runtime.shutdown_task = asyncio.create_task(
            request.app.state.runtime.shutdown_if_idle()
        )
    return {"ok": released, **snapshot}


@router.post("/v1/responses")
async def responses(request: Request) -> Response:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": {"message": "Invalid JSON request"}}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": {"message": "Request body must be an object"}}, status_code=400)
    work = asyncio.create_task(execute(request.app.state.runtime, body, dict(request.headers)))
    try:
        while not work.done():
            await asyncio.wait({work}, timeout=0.1)
            if not work.done() and await request.is_disconnected():
                work.cancel()
                return Response(status_code=499)
        result = await work
        response_type = JSONResponse if result.media_type == "application/json" else Response
        return response_type(
            result.content,
            status_code=result.status_code,
            media_type=result.media_type,
            headers=result.headers,
        )
    finally:
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)


def create_app(runtime_factory=Runtime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = runtime_factory()
        application.state.runtime = runtime
        try:
            await runtime.start()
            yield
        finally:
            await runtime.stop()

    application = FastAPI(title="Local Codex Ollama Router", lifespan=lifespan)
    application.include_router(router)
    return application


app = create_app()


def main() -> None:
    uvicorn.run(app, host=SETTINGS.host, port=SETTINGS.port, log_level="info")


if __name__ == "__main__":
    main()
