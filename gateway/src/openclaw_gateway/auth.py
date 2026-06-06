"""Ed25519 signature verification for the node handshake.

The node-side `DeviceIdentity.sign_connect` (in
:mod:`openclaw_node.identity`) signs the v3 connect payload over
``v3|deviceId|clientId|clientMode|role|scopes|signedAtMs|token|nonce|platform|deviceFamily``.
The gateway must reconstruct that payload from the connect request params,
look up the device's public key, and verify the signature.

Constants here MUST stay in sync with the node-side ones. Both halves
live in this repo (per Rob 2026-06-06), so drift is a build-break, not a
runtime surprise.
"""

from __future__ import annotations

import base64
from typing import Any, Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_CLIENT_ID: Final[str] = "openclaw-hass-node"
_CLIENT_MODE: Final[str] = "node"
_PLATFORM: Final[str] = "linux"
_DEVICE_FAMILY: Final[str] = "hass-node"

# Time window for accepting a signed_at_ms timestamp, in milliseconds.
_SIGNED_AT_TOLERANCE_MS: Final[int] = 5 * 60 * 1000


class AuthError(Exception):
    """Raised when handshake verification fails."""

    def __init__(self, code: str, message: str) -> None:
        """Initialise with a stable error code and human message.

        Args:
            code: Stable wire-style error code.
            message: Human-readable detail.
        """
        super().__init__(message)
        self.code = code
        self.message = message


def _b64url_decode(s: str) -> bytes:
    """Decode a base64url string with optional padding."""
    pad = -len(s) % 4
    return base64.urlsafe_b64decode(s + ("=" * pad))


def verify_connect_signature(
    params: dict[str, Any],
    *,
    nonce_expected: str,
    now_ms: int,
) -> str:
    """Verify the connect-request signature and return the device id.

    Args:
        params: The ``params`` dict from the node's ``connect`` request.
        nonce_expected: The nonce the gateway issued in connect.challenge.
        now_ms: Current Unix time in milliseconds (injectable for tests).

    Returns:
        The verified device_id.

    Raises:
        AuthError: On any field missing, nonce mismatch, time skew, bad
            public key, or signature verification failure. The wire code
            is stable so the gateway can map it to a connect res error.
    """
    device = params.get("device") or {}
    device_id = str(device.get("id", ""))
    public_key_b64 = str(device.get("publicKey", ""))
    signature_b64 = str(device.get("signature", ""))
    nonce = str(device.get("nonce", ""))
    signed_at_ms_raw = device.get("signedAt")

    if not (device_id and public_key_b64 and signature_b64 and nonce):
        raise AuthError("AUTH_MISSING_FIELD", "device payload missing required fields")

    if nonce != nonce_expected:
        raise AuthError("AUTH_NONCE_MISMATCH", "device nonce did not match challenge")

    if signed_at_ms_raw is None:
        raise AuthError("AUTH_BAD_SIGNED_AT", "signedAt is missing")
    try:
        signed_at_ms = int(signed_at_ms_raw)
    except (TypeError, ValueError) as exc:
        raise AuthError("AUTH_BAD_SIGNED_AT", "signedAt is not an integer") from exc

    if abs(now_ms - signed_at_ms) > _SIGNED_AT_TOLERANCE_MS:
        raise AuthError(
            "AUTH_TIME_SKEW",
            "signedAt is outside the accepted window",
        )

    role = str(params.get("role", ""))
    scopes = params.get("scopes") or []
    if not isinstance(scopes, list):
        raise AuthError("AUTH_BAD_SCOPES", "scopes must be a list")
    scopes_str = ",".join(str(s) for s in scopes)
    token = str((params.get("auth") or {}).get("token", ""))

    payload = "|".join(
        [
            "v3",
            device_id,
            _CLIENT_ID,
            _CLIENT_MODE,
            role,
            scopes_str,
            str(signed_at_ms),
            token,
            nonce,
            _PLATFORM,
            _DEVICE_FAMILY,
        ]
    )

    try:
        pub_bytes = _b64url_decode(public_key_b64)
        sig_bytes = _b64url_decode(signature_b64)
    except (ValueError, TypeError) as exc:
        raise AuthError("AUTH_BAD_ENCODING", "publicKey or signature not base64url") from exc

    try:
        Ed25519PublicKey.from_public_bytes(pub_bytes).verify(sig_bytes, payload.encode("utf-8"))
    except InvalidSignature as exc:
        raise AuthError("AUTH_BAD_SIGNATURE", "signature verification failed") from exc
    except ValueError as exc:
        raise AuthError("AUTH_BAD_PUBLIC_KEY", "publicKey is not a valid Ed25519 key") from exc

    return device_id
