"""GET /events - Server-Sent Events stream for the live dashboard."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from gateway.runtime import sse_observer

router = APIRouter()


@router.get("/events")
async def events():
    q = sse_observer.register()

    async def gen():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            sse_observer.unregister(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
