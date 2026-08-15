"""RBAC + dashboard session auth (password + OIDC scaffolding).

RBAC model:
  - workspace API keys carry roles (default ["member"]); admin keys ["admin"].
  - admin/CRUD endpoints require: platform-admin token (X-Admin-Token) OR an
    api-key whose roles include "admin".
  - a signed session JWT (HS256) backs the dashboard login (password or OIDC).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException, Request, status

from gateway.config import settings
from gateway.core.auth import resolve_workspace


# ── tiny HS256 JWT (no external dep) ──
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_session(sub: str, roles: list[str], ttl: int = 8 * 3600,
                  sid: str | None = None, name: str | None = None) -> str:
    now = int(time.time())
    claims: dict = {"sub": sub, "roles": roles, "exp": now + ttl, "iat": now}
    # Optional session id + display name carried in the (signed) cookie so a
    # session can be identified across server restarts.
    if sid:
        claims["sid"] = sid
    if name is not None:
        claims["name"] = name
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    signing = f"{header}.{payload}".encode()
    sig = _b64(hmac.new(settings.session_secret.encode(), signing, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def verify_session(token: str) -> dict | None:
    try:
        header, payload, sig = token.split(".")
        signing = f"{header}.{payload}".encode()
        expected = _b64(hmac.new(settings.session_secret.encode(), signing, hashlib.sha256).digest())
        if not hmac.compare_digest(expected, sig):
            return None
        data = json.loads(_b64d(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def looks_like_jwt(token: str) -> bool:
    """A workspace JWT has exactly two dots and a decodable JWT header; our
    SHA-256 API keys (gw-key-…) never do."""
    if token.count(".") != 2:
        return False
    try:
        hdr = json.loads(_b64d(token.split(".")[0]))
        return isinstance(hdr, dict) and "alg" in hdr
    except Exception:
        return False


def decode_bearer_jwt(token: str) -> dict | None:
    """Return JWT claims, or None. Two modes:
      - dev-trust (AGNOS_JWT_DEV_TRUST=true): decode-without-verify (local/demo);
      - verify: validate RS256 signature against OIDC_ISSUER's JWKS (needs PyJWT)."""
    if settings.jwt_dev_trust:
        try:
            claims = json.loads(_b64d(token.split(".")[1]))
            if claims.get("exp") and claims["exp"] < time.time():
                return None
            return claims
        except Exception:
            return None
    if settings.oidc_issuer:
        try:
            import jwt as pyjwt
            from jwt import PyJWKClient
            jwks = PyJWKClient(settings.oidc_issuer.rstrip("/") + "/.well-known/jwks.json")
            key = jwks.get_signing_key_from_jwt(token).key
            return pyjwt.decode(token, key, algorithms=["RS256", "ES256"],
                                options={"verify_aud": False})
        except Exception:
            return None
    return None


async def require_admin(request: Request) -> dict:
    """Allow if platform-admin token, an admin session cookie, or an admin api-key."""
    # 1) platform admin token (constant-time compare)
    admin_token = request.headers.get("x-admin-token")
    if admin_token and hmac.compare_digest(admin_token, settings.platform_admin_token):
        return {"principal": "platform-admin", "roles": ["admin"]}
    # 2) dashboard session cookie
    cookie = request.cookies.get("agnos_session")
    if cookie:
        data = verify_session(cookie)
        if data and "admin" in data.get("roles", []):
            return {"principal": data["sub"], "roles": data["roles"]}
    # 3) admin api-key
    auth = request.headers.get("authorization")
    if auth:
        try:
            ws = await resolve_workspace(auth)
            if "admin" in (ws.roles or []):
                return {"principal": ws.workspace_id, "roles": ws.roles}
        except HTTPException:
            pass
    raise HTTPException(status.HTTP_403_FORBIDDEN,
                        detail={"error": {"message": "Admin privileges required "
                                                     "(X-Admin-Token, admin session, or admin api-key).",
                                          "type": "permission_denied"}})


async def is_admin_request(request: Request) -> bool:
    """Soft check: True if the request would pass require_admin, False otherwise.

    Used by routes that have dual auth (admin OR workspace key) to branch
    behavior without raising on the non-admin path.
    """
    try:
        await require_admin(request)
        return True
    except HTTPException:
        return False
