"""Workspace credential store - envelope encryption under a single master key.

The ONLY secret loaded at startup is GATEWAY_MASTER_KEY. Per-workspace provider
credentials are encrypted with Fernet under that master key and stored in our
Postgres. They are decrypted on demand (per request / on Bifrost sync) and
cached briefly. This is the source of truth; Bifrost holds only a synced copy.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet

from gateway.config import settings


def _derive_fernet_key(master: str) -> bytes:
    """Accept any passphrase; derive a valid 32-byte url-safe base64 Fernet key.

    If the passphrase already is a valid Fernet key, use it as-is.
    """
    raw = master.encode()
    try:
        if len(base64.urlsafe_b64decode(raw)) == 32:
            return raw
    except Exception:
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


class CredentialCipher:
    """Symmetric envelope encryption for credential dicts."""

    def __init__(self, master_key: str | None = None) -> None:
        key = master_key or settings.master_key
        if not key:
            raise RuntimeError("GATEWAY_MASTER_KEY is not set.")
        self._fernet = Fernet(_derive_fernet_key(key))

    def encrypt(self, credentials: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(credentials).encode())

    def decrypt(self, blob: bytes) -> dict[str, Any]:
        return json.loads(self._fernet.decrypt(blob).decode())


_cipher: CredentialCipher | None = None


def cipher() -> CredentialCipher:
    global _cipher
    if _cipher is None:
        _cipher = CredentialCipher()
    return _cipher
