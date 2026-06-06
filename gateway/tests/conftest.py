"""Shared test fixtures and helpers for openclaw_gateway."""

from __future__ import annotations

import base64
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def b64url(data: bytes) -> str:
    """Encode bytes as base64url (no padding)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def signed_connect_params(
    *,
    device_id: str = "node-test",
    nonce: str = "abc123",
    role: str = "node",
    scopes: tuple[str, ...] = (),
    token: str = "",
    signed_at_ms: int | None = None,
    private: Ed25519PrivateKey | None = None,
) -> dict[str, Any]:
    """Build connect-req params with a valid Ed25519 signature.

    Args:
        device_id: Device ID in the request.
        nonce: Nonce signed into the payload.
        role: Role string.
        scopes: Scopes list.
        token: Device token (empty on first pairing).
        signed_at_ms: Override signed timestamp; defaults to time.time().
        private: Override the Ed25519 key; defaults to a fresh one.

    Returns:
        A dict shaped like the node's connect request params.
    """
    if signed_at_ms is None:
        signed_at_ms = int(time.time() * 1000)
    if private is None:
        private = Ed25519PrivateKey.generate()
    pub_raw = private.public_key().public_bytes_raw()
    pub_b64 = b64url(pub_raw)
    scopes_str = ",".join(scopes)
    payload = "|".join(
        [
            "v3",
            device_id,
            "openclaw-hass-node",
            "node",
            role,
            scopes_str,
            str(signed_at_ms),
            token,
            nonce,
            "linux",
            "hass-node",
        ]
    )
    sig = private.sign(payload.encode("utf-8"))
    return {
        "role": role,
        "scopes": list(scopes),
        "auth": {"token": token},
        "device": {
            "id": device_id,
            "publicKey": pub_b64,
            "signature": b64url(sig),
            "nonce": nonce,
            "signedAt": signed_at_ms,
        },
    }
