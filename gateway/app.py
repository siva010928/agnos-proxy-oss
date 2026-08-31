"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from gateway import __version__
from gateway.config import settings
from gateway.db.database import init_db
from gateway.db.seed import seed
from gateway.routes import (
    admin, admin_crud, auth as auth_routes, batch, chat, embeddings, events,
    governance as governance_routes, health, models, passthrough, playground,
    utils,
)
from gateway.runtime import bus, engine

_log = structlog.get_logger("gateway")


def validate_config() -> None:
    """Fail fast with a clear message on missing/invalid critical config."""
    problems = []
    if not settings.master_key:
        problems.append("GATEWAY_MASTER_KEY is not set (required to decrypt provider creds)")
    if settings.engine not in ("bifrost", "litellm", "portkey", "direct", "echo"):
        problems.append(f"ENGINE must be one of bifrost|litellm|portkey|direct|echo (got '{settings.engine}')")
    if not settings.db_url:
        problems.append("GOVERNANCE_DB_URL is not set")
    # Fail closed on shipped-default secrets in a login-protected (non-preview) deployment.
    # In PREVIEW_MODE (the public demo) the dashboard is intentionally open, so these dev
    # defaults are expected; anywhere login is enforced they are a full-compromise risk.
    if not settings.preview_mode:
        if settings.platform_admin_token == "platform-admin-secret":
            problems.append("PLATFORM_ADMIN_TOKEN is still the shipped default - set a strong secret")
        if settings.session_secret == "agnos-proxy-session-secret":
            problems.append("SESSION_SECRET is still the shipped default - set a strong random value")
        if settings.dashboard_admin_password == "agnos":
            problems.append("DASHBOARD_ADMIN_PASSWORD is still the dev default - set a strong password")
        if settings.jwt_dev_trust and not settings.oidc_issuer:
            _log.warning("insecure_jwt_dev_trust",
                         msg="AGNOS_JWT_DEV_TRUST=true decodes workspace JWTs WITHOUT signature "
                             "verification; set AGNOS_JWT_DEV_TRUST=false and OIDC_ISSUER in production")
    if problems:
        raise RuntimeError("Startup config invalid:\n  - " + "\n  - ".join(problems))


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config()
    from gateway.core import log_buffer
    log_buffer.install()
    await init_db()
    await seed()
    try:
        from gateway.db.seed import reconcile_components
        n = await reconcile_components()
        _log.info("components_reconciled", workspaces=n)
    except Exception as exc:  # noqa: BLE001
        _log.warning("component_reconcile_failed", error=str(exc))
    # WAVE 25 TRACK 1 - sync FX rates from frankfurter.app (free, no key)
    try:
        from gateway.core.fx import sync_rates, _load_cache_from_db
        await _load_cache_from_db()
        n = await sync_rates(days_back=35)
        _log.info("fx_rates_synced", rates=n)
    except Exception as exc:  # noqa: BLE001
        _log.warning("fx_sync_failed", error=str(exc))
    await bus().start()
    # Sync live model pricing (LiteLLM dataset) in the background
    try:
        from gateway.core.pricing import sync_pricing
        n = await sync_pricing()
        _log.info("pricing_synced", models=n)
    except Exception as exc:  # noqa: BLE001
        _log.warning("pricing_sync_failed", error=str(exc))
    try:
        from gateway.core.pricing import load_overrides
        n = await load_overrides()
        _log.info("pricing_overrides_loaded", overrides=n)
    except Exception as exc:  # noqa: BLE001
        _log.warning("pricing_overrides_failed", error=str(exc))
    # Gateway-wide engine routing (rented↔owned per provider) into the hot-path cache.
    try:
        from gateway.core.engine_routing import load as load_engine_routing
        ov = await load_engine_routing()
        _log.info("engine_routing_loaded", providers=len(ov))
    except Exception as exc:  # noqa: BLE001
        _log.warning("engine_routing_load_failed", error=str(exc))
    # Persisted ACTIVE engine: whatever was last activated must survive a restart.
    try:
        from gateway.core import engine_state
        active = await engine_state.load()
        if active:
            import gateway.runtime as _rt
            _rt._engine = _rt.engine_by_name(active)
            _log.info("active_engine_loaded", engine=active)
    except Exception as exc:  # noqa: BLE001
        _log.warning("active_engine_load_failed", error=str(exc))
    # Seed the model catalog AFTER pricing sync so catalog rows carry synced
    # (LiteLLM) prices - model ids come from data/provider_catalog.yaml, prices
    # from the synced table; no hardcoded model data in code.
    try:
        from gateway.core.model_catalog import seed_catalog_if_empty
        n = await seed_catalog_if_empty()
        if n:
            _log.info("model_catalog_seeded", models=n)
    except Exception as exc:  # noqa: BLE001
        _log.warning("model_catalog_seed_failed", error=str(exc))
    # Pre-warm Presidio (spaCy NLP) off the event loop so the FIRST PII-guarded
    # request doesn't pay the ~2-3s model-load cold start. Best-effort; if
    # presidio isn't installed this is a no-op.
    try:
        import asyncio as _asyncio
        async def _warm_presidio():
            try:
                from gateway.core.guardrails.presidio_detector import _analyzer
                await _asyncio.to_thread(_analyzer)
                _log.info("presidio_prewarmed")
            except Exception:  # noqa: BLE001
                pass
        _asyncio.create_task(_warm_presidio())
    except Exception:  # noqa: BLE001
        pass
    # Pre-warm the DirectEngine Bedrock client off the loop so the first in-process
    # call doesn't pay botocore's one-time data load (a cold-start latency outlier).
    try:
        import asyncio as _aio2
        from gateway.engines import direct_bedrock as _db
        _aio2.create_task(_aio2.to_thread(_db.prewarm))
    except Exception:  # noqa: BLE001
        pass
    # Legacy Bifrost reconcile hook - now a no-op (engines are stateless; the provider
    # key is injected per request, never registered in Bifrost). Kept so startup wiring
    # doesn't break; reconcile_all() returns 0.
    if settings.engine == "bifrost":
        import asyncio as _asyncio
        async def _bifrost_reconcile_bg():
            try:
                from gateway.bifrost.sync import reconcile_all
                import time as _t
                t0 = _t.monotonic()
                n = await reconcile_all()
                _log.info("bifrost_reconcile", keys=n, seconds=round(_t.monotonic() - t0, 1))
            except Exception as exc:  # noqa: BLE001
                _log.warning("bifrost_reconcile_failed", error=str(exc))
        # Run in the BACKGROUND so it never blocks readiness. It's a no-op today (no
        # engine-side keys exist); retained only for backward compatibility. Set
        # BIFROST_RECONCILE_BLOCKING=true to await it.
        if getattr(settings, "bifrost_reconcile_blocking", False):
            await _bifrost_reconcile_bg()
        else:
            _asyncio.create_task(_bifrost_reconcile_bg())
    # Legacy LiteLLM key/model reconcile. The LiteLLM engine is now STATELESS -
    # keys are injected per request (no store_model_in_db), so litellm_sync is a
    # retired no-op; this call is kept only so existing startup wiring doesn't break.
    import asyncio as _aio_ll

    async def _litellm_reconcile_bg():
        try:
            from gateway import litellm_sync
            if not await litellm_sync.healthy():
                return
            import time as _t
            t0 = _t.monotonic()
            n = await litellm_sync.reconcile_all()
            _log.info("litellm_engine_reconcile", models=n, seconds=round(_t.monotonic() - t0, 1))
        except Exception as exc:  # noqa: BLE001
            _log.warning("litellm_engine_reconcile_failed", error=str(exc))
    _aio_ll.create_task(_litellm_reconcile_bg())
    # Always-on provider-health background loop (cheap periodic probe)
    _health_task = None
    if settings.provider_health_interval > 0:
        import asyncio
        from gateway.core import provider_health as ph

        async def _health_loop():
            while True:
                try:
                    await ph.snapshot(force=True)
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(settings.provider_health_interval)
        _health_task = asyncio.create_task(_health_loop())
    _log.info("gateway_started", engine=engine().name, port=settings.port)
    yield
    if _health_task:
        _health_task.cancel()
    # graceful shutdown: drain buffered governance events, then stop workers
    try:
        pending = await bus().drain(timeout=settings.shutdown_drain_seconds)
        _log.info("governance_drained", pending=pending)
    except Exception as exc:  # noqa: BLE001
        _log.warning("governance_drain_failed", error=str(exc))
    await bus().stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Agnos Proxy",
                  description="OpenAI-compatible governance proxy over a swappable BackendEngine.",
                  version=__version__, lifespan=lifespan)
    # OpenTelemetry tracing (auto-instrument FastAPI + explicit pipeline spans)
    try:
        from gateway.core.tracing import setup_tracing, instrument_fastapi
        setup_tracing()
        instrument_fastapi(app)
    except Exception as exc:  # noqa: BLE001
        _log.warning("otel_setup_failed", error=str(exc))
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    # Prod-grade safety net: never leak a bare, unstructured 500. An unhandled
    # exception escaping a route is the GATEWAY's bug (not the upstream's), so we
    # return 500 with `gateway_internal_error` - distinct from 502 `upstream_error`.
    # The full traceback is logged so we can grep + fix; the client gets a clean,
    # parseable OpenAI-shaped error.
    from fastapi import Request as _Req
    from fastapi.responses import JSONResponse as _JSON
    @app.exception_handler(Exception)
    async def _unhandled(request: _Req, exc: Exception):  # noqa: ANN001
        import traceback
        _log.error("unhandled_exception", path=str(request.url.path),
                   error=str(exc), traceback=traceback.format_exc()[-2000:])
        return _JSON(status_code=500, content={"error": {
            "type": "gateway_internal_error",
            "message": f"Gateway encountered an unexpected error: {str(exc)[:200]}",
        }})
    try:
        from starlette.middleware.sessions import SessionMiddleware
        app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    except Exception:  # noqa: BLE001
        pass
    app.include_router(auth_routes.router, tags=["auth"])
    app.include_router(chat.router, tags=["openai-compatible"])
    app.include_router(embeddings.router, tags=["openai-compatible"])
    app.include_router(batch.router, tags=["openai-compatible"])
    app.include_router(utils.router, tags=["openai-compatible"])
    app.include_router(passthrough.router, tags=["gateway-native"])
    app.include_router(models.router, tags=["openai-compatible"])
    app.include_router(events.router, tags=["gateway-native"])
    app.include_router(governance_routes.router, tags=["gateway-native"])
    app.include_router(admin.router)
    app.include_router(admin_crud.router)
    app.include_router(playground.router)
    app.include_router(health.router, tags=["gateway-native"])

    _root = Path(__file__).resolve().parent.parent
    _react_dist = _root / "frontend" / "dist"
    _single = _root / "dashboard" / "index.html"

    # Optional static media dir served OUTSIDE the JS bundle and OUTSIDE git.
    # StaticFiles supports HTTP range requests (stream/seek large files). The dir is
    # created if missing so the mount never crashes; in prod it can be bind-mounted
    # in (see deploy/docker-compose.prod.yml).
    _media = _root / "media"
    try:
        _media.mkdir(parents=True, exist_ok=True)
        app.mount("/media", StaticFiles(directory=str(_media)), name="media")
    except Exception:  # noqa: BLE001 - never let media mounting block startup
        pass

    if (_react_dist / "index.html").exists():
        # Serve hashed assets, then fall back to index.html for all SPA client routes
        # (so deep links like /app/cost survive a hard refresh).
        app.mount("/app/assets", StaticFiles(directory=str(_react_dist / "assets")), name="assets")
        _index = str(_react_dist / "index.html")

        @app.get("/app", include_in_schema=False)
        @app.get("/app/{path:path}", include_in_schema=False)
        async def spa(path: str = ""):
            f = _react_dist / path
            if path and f.is_file():
                return FileResponse(str(f))
            # index.html must NOT be cached - it references hashed asset bundles,
            # so a stale cached index points at old JS and the user sees an old UI
            # (e.g. saved guardrails appearing unticked). Hashed assets under
            # /app/assets are immutable and cached aggressively by their filename.
            return FileResponse(_index, headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            })

        @app.get("/", include_in_schema=False)
        async def root():
            # Send the site root to the dashboard SPA (the public preview link).
            return RedirectResponse("/app/")
    else:
        @app.get("/", include_in_schema=False)
        async def root_single():
            return FileResponse(str(_single))

    return app


app = create_app()
