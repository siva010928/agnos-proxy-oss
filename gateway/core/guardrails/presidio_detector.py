"""Presidio detector plugin (optional). Lazy-imports presidio_analyzer so the
gateway runs fine without it; when installed, provides ML-based PII detection
(PERSON, LOCATION, CREDIT_CARD, US_SSN, EMAIL, PHONE, etc.)."""
from __future__ import annotations

import threading

from gateway.core.guardrails.detectors import Detector, Finding, _mask

_ANALYZER = None
_ANALYZER_LOCK = threading.Lock()


def _analyzer():
    global _ANALYZER
    if _ANALYZER is None:
        # Detectors now run via asyncio.to_thread, so init can race - guard it.
        with _ANALYZER_LOCK:
            if _ANALYZER is None:
                from presidio_analyzer import AnalyzerEngine
                _ANALYZER = AnalyzerEngine()
    return _ANALYZER


class PresidioDetector(Detector):
    name = "presidio"

    def __init__(self, entities: list[str] | None = None, threshold: float = 0.5):
        self.entities = entities
        self.threshold = threshold

    def scan(self, text: str) -> list[Finding]:
        try:
            results = _analyzer().analyze(text=text, entities=self.entities, language="en")
        except Exception:
            return []
        out = []
        for r in results:
            if r.score >= self.threshold:
                out.append(Finding(self.name, r.entity_type.lower(), _mask(text[r.start:r.end])))
        return out

    def redact(self, text: str) -> tuple[str, list[Finding]]:
        try:
            results = sorted(_analyzer().analyze(text=text, entities=self.entities, language="en"),
                             key=lambda r: r.start, reverse=True)
        except Exception:
            return text, []
        findings = []
        for r in results:
            if r.score >= self.threshold:
                findings.append(Finding(self.name, r.entity_type.lower(), _mask(text[r.start:r.end])))
                text = text[:r.start] + f"[REDACTED:{r.entity_type.lower()}]" + text[r.end:]
        return text, findings
