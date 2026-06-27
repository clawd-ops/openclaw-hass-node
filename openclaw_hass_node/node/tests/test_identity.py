"""Tests for persisted device identity."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
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


def _file_mode(path: Path) -> int:
    """Return the permission bits of *path* (last 9 bits of st_mode)."""
    return stat.S_IMODE(path.stat().st_mode)


def test_save_identity_writes_with_mode_0600(tmp_path: Path) -> None:
    """The persisted Ed25519 key must be 0o600, not the umask default."""
    path = tmp_path / "node-key.json"
    identity = generate_identity()
    save_identity(identity, path)
    assert _file_mode(path) == 0o600


def test_save_identity_tightens_existing_loose_permissions(tmp_path: Path) -> None:
    """Overwriting a previously-loose 0o644 key file must end at 0o600."""
    path = tmp_path / "node-key.json"
    identity = generate_identity()
    save_identity(identity, path)
    path.chmod(0o644)
    save_identity(identity, path)
    assert _file_mode(path) == 0o600


def test_load_identity_tightens_legacy_loose_permissions(tmp_path: Path) -> None:
    """Reading a legacy 0o644 key file must chmod it down to 0o600."""
    path = tmp_path / "node-key.json"
    identity = generate_identity()
    save_identity(identity, path)
    path.chmod(0o644)
    load_identity(path)
    assert _file_mode(path) == 0o600


def test_save_identity_refuses_symlink_at_path(tmp_path: Path) -> None:
    """A symlink planted at the key path must be rejected, not written through."""
    real_target = tmp_path / "elsewhere.json"
    real_target.write_text("untouched")
    link = tmp_path / "node-key.json"
    link.symlink_to(real_target)
    identity = generate_identity()
    with pytest.raises(OSError):
        save_identity(identity, link)
    # The target was not touched — the private key did not leak through the symlink.
    assert real_target.read_text() == "untouched"


def test_save_identity_overwrites_existing_loose_temp_to_0600(tmp_path: Path) -> None:
    """Writing over a pre-existing 0o644 key file must end at 0o600 with new content."""
    path = tmp_path / "node-key.json"
    path.write_text("stale-non-key-content")
    path.chmod(0o644)
    identity = generate_identity()
    save_identity(identity, path)
    assert _file_mode(path) == 0o600
    assert path.read_text() != "stale-non-key-content"


def test_load_identity_does_not_chmod_through_symlink(tmp_path: Path) -> None:
    """Loading via a symlinked key path must not chmod the symlink target."""
    real = tmp_path / "real-key.json"
    identity = generate_identity()
    save_identity(identity, real)
    # Set target to 0o644; load via symlink should NOT silently tighten target.
    real.chmod(0o644)
    link = tmp_path / "link-key.json"
    link.symlink_to(real)
    load_identity(link)
    # If the symlink was followed for chmod, real would now be 0o600.
    assert _file_mode(real) == 0o644
