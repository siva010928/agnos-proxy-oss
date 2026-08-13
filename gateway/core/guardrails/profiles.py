"""Detector factory: build a Detector from a GuardrailProfile (detector_type + config).

Native detectors run in our layer (regex/secrets/keyword/presidio). External
detectors (Bedrock Guardrails live; Azure Content Safety / Google Model Armor
scaffolded) call third-party policy APIs. Everything is evaluated inside the
gateway - never delegated to the translation engine.
"""
from __future__ import annotations

from gateway.core.guardrails.detectors import (
    Detector, Finding, KeywordDetector, RegexPIIDetector, SecretsDetector,
)


class NotConfigured(Exception):
    """Raised when an external detector profile lacks the creds/policy id it needs."""


class BedrockGuardrailDetector(Detector):
    """Live AWS Bedrock Guardrails via the ApplyGuardrail API.

    config: {guardrail_id, guardrail_version?, region?, access_key?, secret_key?}.
    Creds fall back to the gateway's AWS env settings.
    """
    name = "bedrock"

    def __init__(self, config: dict):
        from gateway.core.provider_test import clean_guardrail_id, clean_guardrail_version
        raw_gid = config.get("guardrail_id") or config.get("guardrailIdentifier")
        self.gid = clean_guardrail_id(raw_gid or "")
        self.version = clean_guardrail_version(config.get("guardrail_version", "DRAFT"))
        self.config = config
        if not self.gid:
            raise NotConfigured("Bedrock guardrail profile needs config.guardrail_id")

    def _client(self):
        import boto3
        from gateway.config import settings
        return boto3.client(
            "bedrock-runtime",
            region_name=self.config.get("region") or settings.aws_region_name,
            aws_access_key_id=self.config.get("access_key") or settings.aws_access_key_id,
            aws_secret_access_key=self.config.get("secret_key") or settings.aws_secret_access_key,
            aws_session_token=self.config.get("session_token") or getattr(settings, "aws_session_token", None),
        )

    def scan(self, text: str) -> list[Finding]:
        if not text.strip():
            return []
        try:
            resp = self._client().apply_guardrail(
                guardrailIdentifier=self.gid, guardrailVersion=self.version,
                source="INPUT", content=[{"text": {"text": text}}])
        except Exception as exc:  # noqa: BLE001
            raise NotConfigured(f"Bedrock ApplyGuardrail failed: {exc}")
        if resp.get("action") == "GUARDRAIL_INTERVENED":
            cats = []
            for a in resp.get("assessments", []):
                for k in ("topicPolicy", "contentPolicy", "sensitiveInformationPolicy",
                          "wordPolicy", "contextualGroundingPolicy"):
                    if a.get(k):
                        cats.append(k.replace("Policy", ""))
            # Surface the guardrail's CONFIGURED response (the "blocked messaging"
            # text set in the AWS console) verbatim instead of a generic message.
            blocked_msg = None
            outs = resp.get("outputs") or resp.get("output") or []
            if isinstance(outs, list) and outs:
                first = outs[0]
                blocked_msg = (first.get("text") if isinstance(first, dict) else None) or None
            return [Finding(self.name, c or "bedrock_guardrail", "***intervened***", message=blocked_msg)
                    for c in (cats or ["bedrock_guardrail"])]
        return []


class _ScaffoldDetector(Detector):
    """External provider scaffold - returns 'not configured' until creds/policy supplied."""
    def __init__(self, name: str, config: dict):
        self.name = name
        if not (config.get("api_key") or config.get("endpoint") or config.get("policy_id")):
            raise NotConfigured(f"{name} detector not configured (needs api_key/endpoint/policy_id)")

    def scan(self, text: str) -> list[Finding]:  # pragma: no cover - external, untested
        return []


# regex PII template defaults (used when a regex profile gives no custom patterns)
def build_detector(detector_type: str, config: dict | None) -> Detector:
    config = config or {}
    if detector_type == "regex":
        det = RegexPIIDetector()
        if config.get("patterns"):
            import re as _re
            det.PATTERNS = {k: _re.compile(v) for k, v in config["patterns"].items()}
        return det
    if detector_type == "secrets":
        return SecretsDetector()
    if detector_type == "keyword":
        return KeywordDetector(config.get("keywords") or config.get("words") or [])
    if detector_type == "presidio":
        from gateway.core.guardrails.presidio_detector import PresidioDetector
        return PresidioDetector(entities=config.get("entities"))
    if detector_type == "bedrock":
        return BedrockGuardrailDetector(config)
    if detector_type in ("azure", "model-armor"):
        return _ScaffoldDetector(detector_type, config)
    raise NotConfigured(f"unknown detector_type '{detector_type}'")


DETECTOR_TYPES = ["regex", "secrets", "keyword", "presidio", "bedrock", "azure", "model-armor"]
