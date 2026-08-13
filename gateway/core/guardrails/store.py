"""Guardrail rules store + evaluator (runs entirely in our layer).

Compiles the applicable rules for a request from three sources:
  1. DB GuardrailRules scoped global | workspace | component (+ linked GuardrailProfiles),
  2. per-request selected rule ids (X-Gateway-Guardrail-Ids),
  3. the workspace/component inline guardrail config (flags → an implicit rule).
Then evaluates CEL + sampling + detectors and returns block/redact/audit outcome.
Never delegates to the translation engine.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

from sqlalchemy import or_, select

from gateway.core.guardrails.detectors import Detector, Finding
from gateway.core.guardrails.engine import GuardrailOutcome, _cel_matches, _content_text
from gateway.core.guardrails.profiles import NotConfigured, build_detector
from gateway.db.database import async_session
from gateway.db.models import GuardrailProfile, GuardrailRule


@dataclass
class CompiledRule:
    name: str
    action: str           # block|redact|audit
    apply_to: str         # input|output|both
    cel: str
    sampling_rate: float
    detectors: list[Detector] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # for the Test panel: which condition matched + per-detector findings
    rule_id: int | None = None
    # scope provenance (so callers/telemetry can reason about applicability)
    scope: str = "global"
    workspace_id: str | None = None
    component: str | None = None
    # Whether this rule's `action` was explicitly chosen by the admin (DB rule
    # built in the Rule Builder), vs inherited from a workspace's inline guardrail
    # config (the built-in detector flags). Explicit actions are authoritative -
    # the workspace's enforcement mode does NOT downgrade them. (See _resolve_action.)
    explicit_action: bool = True


_CACHE: dict[tuple, tuple[list, float]] = {}
_TTL = 15.0

# Severity ladder. Used only for the per-REQUEST override (`X-Gateway-Guardrail-Mode`),
# which is a hard operator ceiling; workspace mode is no longer a clamp on explicit
# rule actions (that contradicted the rule's own configuration).
_SEVERITY = {"audit": 0, "redact": 1, "block": 2}


def _resolve_action(rule_action: str, *, explicit: bool, workspace_mode: str | None,
                    request_override: str | None) -> str:
    """Decide the final action for a matched rule.

    Priority (highest first):
      1. **Per-request override** (`X-Gateway-Guardrail-Mode` header) - always wins,
         capped by the severity ladder. Used for operator probes / emergency
         audit-only mode without redeploying.
      2. **Rule's explicit action** - if the admin chose `block` in the Rule Builder,
         it blocks. The workspace's enforcement mode does NOT downgrade it; that
         was a confusing inversion (rule says block, workspace says audit, audit
         won) and is exactly what an admin reported.
      3. **Workspace enforcement mode** - acts as the default for INLINE rules
         (built-in detector flags like `pii_detection`) which don't carry an
         explicit action. If unset, the rule's own action is used.
    """
    if request_override and request_override in _SEVERITY:
        return request_override
    if explicit:
        return rule_action
    if workspace_mode and workspace_mode in _SEVERITY:
        return workspace_mode
    return rule_action


def _rule_applies(scope: str, rule_ws: str | None, rule_component: str | None,
                  workspace_id: str, component: str | None) -> bool:
    """Single source of truth for whether a rule is in scope for this request.

    Mirrors the SQL filter in `_db_rules` so that EVERY path (DB scan, per-request
    selected ids, UI listing) agrees on applicability - no cross-workspace leaks.
    """
    if scope == "global":
        return True
    if scope == "workspace":
        return rule_ws == workspace_id
    if scope == "component":
        return rule_ws == workspace_id and rule_component == component
    return False


def _profile_detector(p: GuardrailProfile):
    return build_detector(p.detector_type, p.config or {})


async def _db_rules(workspace_id: str, component: str | None) -> list[CompiledRule]:
    ck = (workspace_id, component)
    hit = _CACHE.get(ck)
    if hit and hit[1] > time.monotonic():
        return hit[0]
    try:
        async with async_session() as s:
            rules = (await s.scalars(select(GuardrailRule).where(
                GuardrailRule.enabled == True,  # noqa: E712
                or_(GuardrailRule.scope == "global",
                    GuardrailRule.workspace_id == workspace_id)))).all()
            profiles = {p.id: p for p in (await s.scalars(select(GuardrailProfile))).all()}
    except Exception:  # noqa: BLE001 - DB down: no DB rules (inline still applies)
        return []
    compiled = []
    for r in rules:
        if r.scope == "component" and r.component != component:
            continue
        dets, errs = [], []
        for pid in (r.profile_ids or []):
            p = profiles.get(pid)
            if not p or not p.enabled:
                continue
            try:
                dets.append(_profile_detector(p))
            except NotConfigured as exc:
                errs.append(str(exc))
        compiled.append(CompiledRule(name=r.name, action=r.action, apply_to=r.apply_to,
                                     cel=r.cel_expression, sampling_rate=r.sampling_rate,
                                     detectors=dets, errors=errs, rule_id=r.id,
                                     scope=r.scope, workspace_id=r.workspace_id,
                                     component=r.component))
    _CACHE[ck] = (compiled, time.monotonic() + _TTL)
    return compiled


def invalidate() -> None:
    _CACHE.clear()


def _inline_rules(gconf: dict, mode_override: str | None) -> list[CompiledRule]:
    """Translate the workspace/component inline guardrail flags into compiled rules."""
    from gateway.core.guardrails.engine import GuardrailEngine
    eng = GuardrailEngine()
    detectors_by_name = eng._detectors_for(gconf)  # noqa: SLF001
    out = []
    for rule in eng._rules(gconf):  # noqa: SLF001
        dets = [detectors_by_name[d] for d in rule.get("detectors", []) if d in detectors_by_name]
        out.append(CompiledRule(
            name=rule.get("name", "default"), action=mode_override or rule.get("action", "block"),
            apply_to=rule.get("apply_to", "input"), cel=rule.get("cel", "true"),
            sampling_rate=float(rule.get("sampling_rate", 1.0)), detectors=dets,
            # Inline rules don't carry an admin-chosen action - workspace mode IS
            # their default. (Distinguishes them from DB rules built in the Rule
            # Builder, where the admin explicitly chose block/redact/audit.)
            explicit_action=False))
    return out


async def _selected_rules(ids: list[int], workspace_id: str,
                          component: str | None) -> list[CompiledRule]:
    """Load explicitly-selected rules by id, but ENFORCE scope ownership.

    Selecting a rule (via the workspace config `rule_ids` or the
    `X-Gateway-Guardrail-Ids` header) can never smuggle another workspace's
    workspace/component-scoped rule into this request. Out-of-scope ids are
    silently dropped - closing the cross-workspace leak at the runtime layer
    (defense-in-depth on top of the UI no longer offering them).
    """
    if not ids:
        return []
    try:
        async with async_session() as s:
            rules = (await s.scalars(select(GuardrailRule).where(GuardrailRule.id.in_(ids)))).all()
            profiles = {p.id: p for p in (await s.scalars(select(GuardrailProfile))).all()}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rules:
        if not _rule_applies(r.scope, r.workspace_id, r.component, workspace_id, component):
            continue
        dets, errs = [], []
        for pid in (r.profile_ids or []):
            p = profiles.get(pid)
            if p:
                try:
                    dets.append(_profile_detector(p))
                except NotConfigured as exc:
                    errs.append(str(exc))
        out.append(CompiledRule(name=r.name, action=r.action, apply_to=r.apply_to,
                                cel=r.cel_expression, sampling_rate=r.sampling_rate,
                                detectors=dets, errors=errs, rule_id=r.id,
                                scope=r.scope, workspace_id=r.workspace_id,
                                component=r.component))
    return out


def _eval_block_audit(messages, detectors) -> list[Finding]:
    findings = []
    for m in messages:
        text = _content_text(m.get("content"))
        for det in detectors:
            try:
                findings += det.scan(text)
            except Exception:  # noqa: BLE001 - a detector error must not crash the request
                pass
    return findings


def _eval_redact(messages, detectors):
    new_messages, all_findings, changed = [], [], False
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            red = content
            for det in detectors:
                try:
                    red, f = det.redact(red)
                    all_findings += f
                except Exception:  # noqa: BLE001
                    pass
            if red != content:
                changed = True
            nm = dict(m); nm["content"] = red
            new_messages.append(nm)
        else:
            new_messages.append(m)
    return new_messages, all_findings, changed


async def evaluate_input(body: dict, ws_ctx, selected_ids: list[int] | None = None,
                         mode_override: str | None = None,
                         headers: dict | None = None) -> GuardrailOutcome:
    gconf = ws_ctx.guardrails or {}
    # explicit per-request selection has priority, then DB scope rules, then inline defaults
    rules = (await _selected_rules(selected_ids or [], ws_ctx.workspace_id, ws_ctx.component)
             + await _db_rules(ws_ctx.workspace_id, ws_ctx.component)
             + _inline_rules(gconf, mode_override))
    # The per-request `X-Gateway-Guardrail-Mode` header is a hard operator ceiling
    # (e.g. force audit-only across the board for a probe). The workspace's own
    # `mode` is now the DEFAULT for inline rules only - it no longer downgrades a
    # DB rule that explicitly says `block`. This matches the expected mental model:
    # "if I configured Action = BLOCK on this rule, it blocks."
    workspace_mode = gconf.get("mode")
    request_override = mode_override
    messages = body.get("messages", [])
    cel_ctx = {
        "workspace": ws_ctx.workspace_id,
        "component": ws_ctx.component,
        "user": ws_ctx.user_id,
        "headers": headers or {},
    }
    for rule in rules:
        if rule.apply_to not in ("input", "both"):
            continue
        if rule.sampling_rate < 1.0 and random.random() > rule.sampling_rate:
            continue
        if not _cel_matches(rule.cel, body, cel_ctx):
            continue
        if not rule.detectors:
            continue
        action = _resolve_action(rule.action, explicit=rule.explicit_action,
                                 workspace_mode=workspace_mode, request_override=request_override)
        if action == "redact":
            new_messages, findings, changed = await asyncio.to_thread(_eval_redact, messages, rule.detectors)
            if findings:
                return GuardrailOutcome(blocked=False, action="redact", rule=rule.name,
                                        findings=findings,
                                        redacted_messages=new_messages if changed else None)
        else:
            findings = await asyncio.to_thread(_eval_block_audit, messages, rule.detectors)
            if findings:
                return GuardrailOutcome(blocked=(action == "block"), action=action,
                                        rule=rule.name, findings=findings)
    return GuardrailOutcome(blocked=False)


async def evaluate_output(text: str, ws_ctx, headers: dict | None = None) -> list[Finding]:
    """Audit-only scan of model output (never blocks mid-stream)."""
    gconf = ws_ctx.guardrails or {}
    rules = _inline_rules(gconf, None) + await _db_rules(ws_ctx.workspace_id, ws_ctx.component)

    def _scan_all() -> list[Finding]:
        out: list[Finding] = []
        for rule in rules:
            if rule.apply_to not in ("output", "both"):
                continue
            for det in rule.detectors:
                try:
                    out += det.scan(text)
                except Exception:  # noqa: BLE001
                    pass
        return out

    # Offload to a thread: detector scans (esp. Presidio spaCy NER) are CPU-bound
    # and would otherwise block the event loop / serialize concurrent requests.
    return await asyncio.to_thread(_scan_all)


async def test_rule(content: str, cel: str, profiles: list[dict], action: str = "block",
                    headers: dict | None = None, model: str | None = None) -> dict:
    """Real evaluation for the UI Test panel - runs the rule's CEL + each linked
    detector against sample content, returning matched-condition info, per-detector
    findings (with processing time), and a recommended action."""
    body = {"messages": [{"role": "user", "content": content}]}
    if model:
        body["model"] = model
    cel_ctx = {"workspace": "", "component": "", "user": "", "headers": headers or {}}
    t0 = time.monotonic()
    cel_ok = _cel_matches(cel or "true", body, cel_ctx)
    cel_ms = round((time.monotonic() - t0) * 1000, 3)

    # build detectors
    dets: list[tuple[str, Detector]] = []
    errors: list[str] = []
    for p in profiles:
        try:
            dets.append((p.get("detector_type", "?"), build_detector(p.get("detector_type"), p.get("config") or {})))
        except NotConfigured as exc:
            errors.append(str(exc))

    # per-detector findings with processing time
    per_detector: list[dict] = []
    all_findings: list[Finding] = []
    if cel_ok:
        for dtype, det in dets:
            t = time.monotonic()
            try:
                fs = det.scan(content)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{dtype}: {exc}")
                fs = []
            ms = round((time.monotonic() - t) * 1000, 3)
            for f in fs:
                per_detector.append({
                    "detector_type": dtype, "detector": f.detector,
                    "category": f.category, "excerpt": f.excerpt,
                    "action": action, "processing_ms": ms,
                })
            all_findings += fs

    return {
        "cel_matched": cel_ok,
        "matched_condition": cel if cel_ok and (cel and cel != "true") else ("(no CEL - applies to all)" if cel_ok else None),
        "violation": bool(all_findings),
        "action": action if all_findings else "pass",
        "findings": per_detector,
        "errors": errors,
        "processing_ms": round((time.monotonic() - t0) * 1000, 3),
        "cel_processing_ms": cel_ms,
    }
