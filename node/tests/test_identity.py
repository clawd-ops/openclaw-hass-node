"""Tests for persisted device identity."""

from __future__ import annotations

from pathlib import Path

from openclaw_node.identity import generate_identity, load_identity, load_or_generate, save_identity


def test_generate_identity_signs_connect_payload() -> None:
    """Generated identities include stable metadata and can sign."""
    identity = generate_identity()
    signature, signed_at = identity.sign_connect(
        nonce="nonce", role="node", scopes=["operator.write"], token=""
    )

    assert len(identity.device_id) == 64
    assert identity.public_key_b64url
    assert signature
    assert signed_at > 0


def test_save_load_and_load_or_generate(tmp_path: Path) -> None:
    """Identity persistence round-trips through JSON."""
    path = tmp_path / "node-key.json"
    identity, created = load_or_generate(path)

    assert created is True
    assert path.exists()

    loaded, second_created = load_or_generate(path)
    assert second_created is False
    assert loaded.device_id == identity.device_id

    explicit = load_identity(path)
    assert explicit.device_id == identity.device_id

    save_identity(identity, path)
    assert load_identity(path).public_key_b64url == identity.public_key_b64url
