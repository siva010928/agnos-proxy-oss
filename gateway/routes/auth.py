"""Dashboard auth - password login (built+tested) + OIDC SSO (config-driven).

OIDC uses Authlib if OIDC_* env is set; without an IdP it stays dormant and the
password login is used. Sessions are signed JWTs in an HttpOnly cookie.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from gateway.config import settings
from gateway.core.security import issue_session, verify_session

router = APIRouter(prefix="/auth")


@router.post("/login")
async def login(request: Request):
    body = await request.json()
    if (body.get("username") == settings.dashboard_admin_user
            and body.get("password") == settings.dashboard_admin_password):
        preview_name = body.get("preview_name") if isinstance(body, dict) else None
        sid = uuid.uuid4().hex
        token = issue_session(settings.dashboard_admin_user, ["admin"], sid=sid, name=preview_name)
        resp = JSONResponse({"ok": True, "roles": ["admin"], "user": settings.dashboard_admin_user})
        resp.set_cookie("agnos_session", token, httponly=True, samesite="lax", max_age=8 * 3600)
        return resp
    raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                        detail={"error": {"message": "Invalid credentials", "type": "authentication_error"}})


@router.post("/preview")
async def preview(request: Request):
    """Preview session - issues a dashboard admin session WITHOUT a password.

    This backs the public preview link: anyone with the URL gets the live
    dashboard (no sign-in wall) while the real admin password stays server-side and
    is never shipped to the browser. Enabled only when PREVIEW_MODE is on; otherwise
    behaves as if the route does not exist so normal password login is enforced.
    """
    if not settings.preview_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail={"error": {"message": "Not found", "type": "not_found"}})
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - empty / non-JSON body is fine
        body = {}
    preview_name = body.get("preview_name") if isinstance(body, dict) else None
    sid = uuid.uuid4().hex
    token = issue_session("preview", ["admin"], sid=sid, name=preview_name)
    resp = JSONResponse({"ok": True, "roles": ["admin"], "user": "preview", "preview": True})
    resp.set_cookie("agnos_session", token, httponly=True, samesite="lax", max_age=8 * 3600)
    return resp


@router.get("/me")
async def me(request: Request):
    data = verify_session(request.cookies.get("agnos_session", ""))
    if not data:
        return JSONResponse({"authenticated": False}, status_code=200)
    return {"authenticated": True, "user": data["sub"], "roles": data["roles"]}


@router.post("/logout")
async def logout(request: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("agnos_session")
    return resp


# ── OIDC SSO (authlib-free; active only when OIDC_* configured) ──
import secrets as _secrets

import httpx

_DISCOVERY: dict = {}


async def _discovery() -> dict:
    global _DISCOVERY
    if not _DISCOVERY and settings.oidc_issuer:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{settings.oidc_issuer}/.well-known/openid-configuration")
            _DISCOVERY = r.json()
    return _DISCOVERY


@router.get("/sso/login")
async def sso_login(request: Request):
    if not settings.oidc_issuer:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED,
                            detail={"error": {"message": "OIDC not configured (set OIDC_ISSUER/CLIENT_ID/SECRET).",
                                              "type": "not_configured"}})
    disc = await _discovery()
    state = _secrets.token_urlsafe(16)
    request.session["oidc_state"] = state
    params = {
        "response_type": "code", "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri, "scope": "openid email profile",
        "state": state,
    }
    q = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
    return RedirectResponse(f"{disc['authorization_endpoint']}?{q}")


@router.get("/sso/callback")
async def sso_callback(request: Request, code: str = "", state: str = ""):
    if not settings.oidc_issuer:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="OIDC not configured")
    if state != request.session.get("oidc_state"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="state mismatch")
    disc = await _discovery()
    async with httpx.AsyncClient(timeout=10) as c:
        tok = (await c.post(disc["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": settings.oidc_redirect_uri,
            "client_id": settings.oidc_client_id, "client_secret": settings.oidc_client_secret,
        })).json()
        ui = (await c.get(disc["userinfo_endpoint"],
                          headers={"Authorization": f"Bearer {tok['access_token']}"})).json()
    sub = ui.get("email") or ui.get("sub") or "sso-user"
    session = issue_session(sub, ["admin"])
    resp = RedirectResponse("/app/")
    resp.set_cookie("agnos_session", session, httponly=True, samesite="lax", max_age=8 * 3600)
    return resp
