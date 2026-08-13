"""Logger observer - structured JSON log line per event."""
from __future__ import annotations

import structlog

from gateway.governance.observer import GovernanceEvent, GovernanceObserver
from gateway.governance.sse_observer import event_to_dict

_log = structlog.get_logger("governance")


class LoggerObserver(GovernanceObserver):
    async def on_event(self, event: GovernanceEvent) -> None:
        d = event_to_dict(event)
        _log.info(d.pop("event_kind", "event"), **d)
