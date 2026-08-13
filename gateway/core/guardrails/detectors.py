"""Detectors - pluggable content scanners. Built-ins: regex PII, secrets, keyword.

Each detector returns a list of Findings (type, excerpt-masked, span replaced
for redaction). Detectors are composed by the GuardrailEngine.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Finding:
    detector: str
    category: str
    excerpt: str   # masked
    # Optional provider-supplied block message (e.g. an AWS Bedrock Guardrail's
    # configured "restricted response" text) to surface verbatim to the caller.
    message: str | None = None


class Detector(ABC):
    name: str = "detector"

    @abstractmethod
    def scan(self, text: str) -> list[Finding]: ...

    def redact(self, text: str) -> tuple[str, list[Finding]]:
        """Default: scan-only (no redaction). Regex detectors override."""
        return text, self.scan(text)


def _mask(s: str) -> str:
    return (s[:4] + "***") if len(s) > 4 else "***"


class RegexPIIDetector(Detector):
    name = "regex_pii"
    PATTERNS = {
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b"),
        "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "us_phone": re.compile(r"\b\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),
    }

    def scan(self, text: str) -> list[Finding]:
        out = []
        for cat, pat in self.PATTERNS.items():
            for m in pat.finditer(text):
                out.append(Finding(self.name, cat, _mask(m.group())))
        return out

    def redact(self, text: str) -> tuple[str, list[Finding]]:
        findings: list[Finding] = []
        for cat, pat in self.PATTERNS.items():
            def _sub(m):
                findings.append(Finding(self.name, cat, _mask(m.group())))
                return f"[REDACTED:{cat}]"
            text = pat.sub(_sub, text)
        return text, findings


class SecretsDetector(Detector):
    """Gitleaks-style high-signal secret patterns."""
    name = "secrets"
    PATTERNS = {
        "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "openai_key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
        "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
        "private_key": re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    }

    def scan(self, text: str) -> list[Finding]:
        out = []
        for cat, pat in self.PATTERNS.items():
            for m in pat.finditer(text):
                out.append(Finding(self.name, cat, _mask(m.group())))
        return out

    def redact(self, text: str) -> tuple[str, list[Finding]]:
        findings: list[Finding] = []
        for cat, pat in self.PATTERNS.items():
            def _sub(m):
                findings.append(Finding(self.name, cat, _mask(m.group())))
                return f"[REDACTED:{cat}]"
            text = pat.sub(_sub, text)
        return text, findings


class KeywordDetector(Detector):
    name = "keyword"

    def __init__(self, words: list[str]):
        self._re = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE) if words else None

    def scan(self, text: str) -> list[Finding]:
        if not self._re:
            return []
        return [Finding(self.name, "blocklist", _mask(m.group())) for m in self._re.finditer(text)]
