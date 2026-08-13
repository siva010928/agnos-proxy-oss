"""Mint a workspace-scoped demo JWT for the gateway (dev-trust mode).

The gateway derives workspace_id / user (sub) / component / roles from the token
server-side - never from the request body. In dev-trust mode the signature is not
verified (local/demo); in production set AGNOS_JWT_DEV_TRUST=false + OIDC_ISSUER
so the gateway verifies the IdP signature instead.

Usage:
  python scripts/mint_demo_jwt.py --workspace ws-novatech-payments --user alice --component document-processing
  curl -H "Authorization: Bearer $(python scripts/mint_demo_jwt.py -w ws-novatech-payments)" ...
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def mint(workspace: str, user: str, component: str | None, roles: list[str], ttl: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"workspace_id": workspace, "sub": user, "roles": roles,
               "iat": int(time.time()), "exp": int(time.time()) + ttl}
    if component:
        payload["component"] = component
    h = _b64(json.dumps(header).encode())
    p = _b64(json.dumps(payload).encode())
    sig = _b64(hmac.new(b"demo-not-verified", f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-w", "--workspace", required=True)
    ap.add_argument("-u", "--user", default="demo-user")
    ap.add_argument("-c", "--component", default=None)
    ap.add_argument("-r", "--roles", default="member", help="comma-separated")
    ap.add_argument("--ttl", type=int, default=8 * 3600)
    args = ap.parse_args()
    print(mint(args.workspace, args.user, args.component, args.roles.split(","), args.ttl))


if __name__ == "__main__":
    main()
