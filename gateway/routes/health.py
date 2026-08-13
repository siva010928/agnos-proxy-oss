"""Health + readiness + provider health."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from gateway.config import settings
from gateway.core import metrics as M
from gateway.db.database import async_session
from gateway.runtime import engine
from sqlalchemy import text

router = APIRouter()


async def _db_ok() -> bool:
    try:
        async with async_session() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.get("/health")
async def health():
    return {"status": "ok", "service": "agnos-proxy-llm-gateway",
            "playground": settings.playground_mode}


@router.get("/health/ready")
async def ready():
    """Readiness: DB + backend engine (+ Bifrost when engine=bifrost). 503 if any down."""
    eng = engine()
    db_ok = await _db_ok()
    engine_ok = await eng.healthcheck()
    checks = {"db": db_ok, "engine": engine_ok, "engine_name": eng.name}
    if settings.engine == "bifrost":
        # engine.healthcheck() already pings Bifrost /api/providers for the bifrost engine
        checks["bifrost"] = engine_ok
    ready_ok = db_ok and engine_ok
    return JSONResponse(status_code=200 if ready_ok else 503,
                        content={"ready": ready_ok, "checks": checks})


@router.get("/health/providers")
async def provider_health(force: bool = False):
    """Per-provider reachability (live 1-token probe, cached) + circuit-breaker state."""
    from gateway.core import provider_health as ph
    eng = engine()
    snap = await ph.snapshot(force=force)
    return {"engine": eng.name, "engine_healthy": await eng.healthcheck(),
            "db_healthy": await _db_ok(), **snap}


@router.get("/metrics")
async def metrics():
    body, ctype = M.render()
    return Response(content=body, media_type=ctype)
