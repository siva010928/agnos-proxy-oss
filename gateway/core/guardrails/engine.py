"""Guardrail engine - Rules (CEL: when) + Detectors (how) + actions.

Workspace guardrail config shape:
  {"pii_detection": true, "mode": "block"|"redact"|"audit",
   "secrets_detection": true, "keywords": ["foo"],
   "rules": [{"name":"...","cel":"true","apply_to":"input","detectors":["regex_pii"],"action":"block"}]}
If no explicit rules: pii_detection/secrets_detection/keywords act as implicit input rules.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from gateway.core.guardrails.detectors import (
    Detector,
    Finding,
    KeywordDetector,
    RegexPIIDetector,
    SecretsDetector,
)

try:
    import celpy  # cel-python
    _CEL: Any = celpy
except Exception:  # noqa: BLE001
    _CEL = None


@dataclass
class GuardrailOutcome:
    blocked: bool = False
    action: str = "audit"          # block|redact|audit
    rule: str = ""
    findings: list[Finding] = field(default_factory=list)
    redacted_messages: list[dict] | None = None


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return str(content)


def _cel_matches(expr: str, body: dict, ctx: dict | None = None) -> bool:
    """Evaluate a CEL expression against the request. The exposed context is
    OUR neutral attribute set (no engine/provider isms):
        request.model, request.workspace, request.component, request.user,
        request.role, request.headers["..."], request.message_text,
        request.message_length, request.messages
    """
    if not expr or expr == "true":
        return True
    if _CEL is None:
        return True  # without celpy, default-apply
    try:
        env = _CEL.Environment()
        ast = env.compile(expr)
        prog = env.program(ast)
        msgs = body.get("messages", []) or []
        # message_text = concatenation of all user-message text blocks (lower-case for contains)
        def _txt(c):
            if isinstance(c, str): return c
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
            return ""
        text = " ".join(_txt(m.get("content")) for m in msgs if isinstance(m, dict))
        request_obj = {
            "model": body.get("model", ""),
            "messages": msgs,
            "message_text": text,
            "message_length": len(text),
            "role": (msgs[0].get("role", "") if msgs else ""),
            "workspace": (ctx or {}).get("workspace", ""),
            "component": (ctx or {}).get("component", ""),
            "user": (ctx or {}).get("user", ""),
            "headers": (ctx or {}).get("headers", {}) or {},
        }
        prog_ctx = {"request": _CEL.json_to_cel(request_obj)}
        return bool(prog.evaluate(prog_ctx))
    except Exception:  # noqa: BLE001
        return True


class GuardrailEngine:
    def _detectors_for(self, gconf: dict) -> dict[str, Detector]:
        d: dict[str, Detector] = {}
        if gconf.get("pii_detection"):
            d["regex_pii"] = RegexPIIDetector()
        if gconf.get("secrets_detection"):
            d["secrets"] = SecretsDetector()
        if gconf.get("keywords"):
            d["keyword"] = KeywordDetector(gconf["keywords"])
        if gconf.get("presidio"):
            try:
                from gateway.core.guardrails.presidio_detector import PresidioDetector
                d["presidio"] = PresidioDetector(entities=gconf.get("presidio_entities"))
            except Exception:  # noqa: BLE001 - optional dep
                pass
        return d

    def _rules(self, gconf: dict) -> list[dict]:
        rules = gconf.get("rules")
        if rules:
            return rules
        # implicit input rule from flags
        dets = list(self._detectors_for(gconf).keys())
        if not dets:
            return []
        return [{"name": "default", "cel": "true", "apply_to": "input",
                 "detectors": dets, "action": gconf.get("mode", "block")}]

    def run_input(self, body: dict, gconf: dict, mode_override: str | None = None) -> GuardrailOutcome:
        rules = self._rules(gconf)
        if not rules:
            return GuardrailOutcome(blocked=False)
        detectors = self._detectors_for(gconf)
        messages = body.get("messages", [])

        for rule in rules:
            if rule.get("apply_to", "input") not in ("input", "both"):
                continue
            # sampling_rate = fraction of matching requests to actually evaluate (0..1)
            sr = rule.get("sampling_rate")
            if sr is not None and random.random() > float(sr):
                continue
            if not _cel_matches(rule.get("cel", "true"), body):
                continue
            action = mode_override or rule.get("action", "block")
            rule_detectors = [detectors[d] for d in rule.get("detectors", []) if d in detectors]

            if action == "redact":
                new_messages = []
                all_findings: list[Finding] = []
                changed = False
                for m in messages:
                    content = m.get("content")
                    if isinstance(content, str):
                        red = content
                        for det in rule_detectors:
                            red, f = det.redact(red)
                            all_findings += f
                        if red != content:
                            changed = True
                        nm = dict(m); nm["content"] = red
                        new_messages.append(nm)
                    else:
                        new_messages.append(m)
                if all_findings:
                    return GuardrailOutcome(blocked=False, action="redact", rule=rule["name"],
                                            findings=all_findings,
                                            redacted_messages=new_messages if changed else None)
            else:  # block | audit
                findings: list[Finding] = []
                for m in messages:
                    text = _content_text(m.get("content"))
                    for det in rule_detectors:
                        findings += det.scan(text)
                if findings:
                    return GuardrailOutcome(blocked=(action == "block"), action=action,
                                            rule=rule["name"], findings=findings)
        return GuardrailOutcome(blocked=False)

    def run_output(self, text: str, gconf: dict) -> list[Finding]:
        """Audit-only scan of model output (never blocks mid-stream)."""
        findings: list[Finding] = []
        for det in self._detectors_for(gconf).values():
            findings += det.scan(text)
        return findings


engine = GuardrailEngine()
