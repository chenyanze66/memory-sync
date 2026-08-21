import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import auth, devices, sync
from .config import get_settings
from .db import cleanup_expired_security_rows, close_pool, open_pool, ping


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        # Cleanup is best-effort; readiness still exposes database outages.
        with suppress(Exception):
            await cleanup_expired_security_rows()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await open_pool()
    cleanup_task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        await close_pool()


app = FastAPI(
    title="Memory Sync API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(sync.router)


@app.middleware("http")
async def enforce_request_size(request: Request, call_next):
    maximum = get_settings().max_request_bytes
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > maximum:
                return JSONResponse(status_code=413, content={"detail": "request body exceeds configured limit"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "invalid content-length"})

    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > maximum:
            return JSONResponse(status_code=413, content={"detail": "request body exceeds configured limit"})
        chunks.append(chunk)
    request._body = b"".join(chunks)
    return await call_next(request)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    if not await ping():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready"}
