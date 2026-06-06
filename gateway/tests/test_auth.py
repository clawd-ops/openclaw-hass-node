"""Tests for openclaw_gateway.auth.verify_connect_signature."""

from __future__ import annotations

import base64

import pytest
from conftest import b64url as _b64url
from conftest import signed_connect_params as _signed_connect_params
from openclaw_gateway.auth import AuthError, verify_connect_signature


def test_verify_succeeds_with_valid_signature() -> None:
    params = _signed_connect_params(device_id="node-a", nonce="n1")
    out = verify_connect_signature(
        params,
        nonce_expected="n1",
        now_ms=params["device"]["signedAt"],
    )
    assert out == "node-a"


def test_verify_rejects_nonce_mismatch() -> None:
    params = _signed_connect_params(nonce="signed-with-this")
    with pytest.raises(AuthError) as ei:
        verify_connect_signature(
            params, nonce_expected="different", now_ms=params["device"]["signedAt"]
        )
    assert ei.value.code == "AUTH_NONCE_MISMATCH"


def test_verify_rejects_missing_device_id() -> None:
    params = _signed_connect_params()
    params["device"]["id"] = ""
    with pytest.raises(AuthError) as ei:
        verify_connect_signature(params, nonce_expected=params["device"]["nonce"], now_ms=0)
    assert ei.value.code == "AUTH_MISSING_FIELD"


def test_verify_rejects_time_skew() -> None:
    params = _signed_connect_params(signed_at_ms=0)
    far_future = 10 * 60 * 1000  # 10 minutes
    with pytest.raises(AuthError) as ei:
        verify_connect_signature(
            params, nonce_expected=params["device"]["nonce"], now_ms=far_future
        )
    assert ei.value.code == "AUTH_TIME_SKEW"


def test_verify_rejects_non_integer_signed_at() -> None:
    params = _signed_connect_params()
    params["device"]["signedAt"] = "not-a-number"
    with pytest.raises(AuthError) as ei:
        verify_connect_signature(params, nonce_expected=params["device"]["nonce"], now_ms=0)
    assert ei.value.code == "AUTH_BAD_SIGNED_AT"


def test_verify_rejects_non_list_scopes() -> None:
    params = _signed_connect_params()
    params["scopes"] = "not-a-list"
    with pytest.raises(AuthError) as ei:
        verify_connect_signature(
            params, nonce_expected=params["device"]["nonce"], now_ms=params["device"]["signedAt"]
        )
    assert ei.value.code == "AUTH_BAD_SCOPES"


def test_verify_rejects_bad_base64() -> None:
    params = _signed_connect_params()
    params["device"]["publicKey"] = "!!!not-base64!!!"
    with pytest.raises(AuthError) as ei:
        verify_connect_signature(
            params, nonce_expected=params["device"]["nonce"], now_ms=params["device"]["signedAt"]
        )
    assert ei.value.code in ("AUTH_BAD_ENCODING", "AUTH_BAD_SIGNATURE", "AUTH_BAD_PUBLIC_KEY")


def test_verify_rejects_tampered_signature() -> None:
    params = _signed_connect_params()
    # Flip a bit in the signature.
    bad = bytearray(base64.urlsafe_b64decode(params["device"]["signature"] + "=="))
    bad[0] ^= 0x01
    params["device"]["signature"] = _b64url(bytes(bad))
    with pytest.raises(AuthError) as ei:
        verify_connect_signature(
            params, nonce_expected=params["device"]["nonce"], now_ms=params["device"]["signedAt"]
        )
    assert ei.value.code == "AUTH_BAD_SIGNATURE"


def test_verify_rejects_bad_public_key_length() -> None:
    params = _signed_connect_params()
    params["device"]["publicKey"] = _b64url(b"too-short")
    with pytest.raises(AuthError) as ei:
        verify_connect_signature(
            params, nonce_expected=params["device"]["nonce"], now_ms=params["device"]["signedAt"]
        )
    assert ei.value.code == "AUTH_BAD_PUBLIC_KEY"


def test_verify_uses_scopes_in_payload() -> None:
    """Tampering with the scopes list invalidates the signature."""
    params = _signed_connect_params(scopes=("a", "b"))
    params["scopes"] = ["a", "c"]  # mismatch
    with pytest.raises(AuthError) as ei:
        verify_connect_signature(
            params, nonce_expected=params["device"]["nonce"], now_ms=params["device"]["signedAt"]
        )
    assert ei.value.code == "AUTH_BAD_SIGNATURE"
