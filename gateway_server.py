"""Agnos Proxy - entry point.  Run: poetry run python gateway_server.py"""
from __future__ import annotations

import uvicorn

from gateway.config import settings

if __name__ == "__main__":
    uvicorn.run("gateway.app:app", host=settings.host, port=settings.port,
                reload=False, log_level=settings.log_level.lower(),
                # force-close lingering connections (e.g. open SSE) so SIGTERM
                # doesn't hang; governance queue is drained in the lifespan.
                timeout_graceful_shutdown=int(settings.shutdown_drain_seconds) + 5)
