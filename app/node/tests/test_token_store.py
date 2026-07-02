"""Tests for the auto-bootstrap local API token store."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from openclaw_node.token_store import (
    generate_local_api_token,
    load_or_generate_local_api_token,
    token_path,
)


def test_generate_local_api_token_is_nonempty() -> None:
    token = generate_local_api_token()
    assert isinstance(token, str)
    assert len(token) > 0


def test_generate_local_api_token_url_safe() -> None:
    """Token must be URL-safe base64 (no +, /, or = chars)."""
    token = generate_local_api_token()
    assert all(c.isalnum() or c in ("-", "_") for c in token), (
        f"Token contains non-URL-safe chars: {token!r}"
    )


def test_generate_local_api_token_unique() -> None:
    tokens = {generate_local_api_token() for _ in range(20)}
    assert len(tokens) == 20, "Tokens must be unique"


def test_token_path_returns_correct_path(tmp_path: Path) -> None:
    assert token_path(tmp_path) == tmp_path / "local-api-token"


def test_load_or_generate_creates_token_on_first_call(tmp_path: Path) -> None:
    token, created = load_or_generate_local_api_token(tmp_path)
    assert created is True
    assert isinstance(token, str)
    assert token


def test_load_or_generate_persists_token(tmp_path: Path) -> None:
    token1, _ = load_or_generate_local_api_token(tmp_path)
    token2, created = load_or_generate_local_api_token(tmp_path)
    assert created is False
    assert token1 == token2


def test_load_or_generate_file_mode(tmp_path: Path) -> None:
    """Persisted token file must have mode 0o600."""
    load_or_generate_local_api_token(tmp_path)
    path = token_path(tmp_path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


def test_load_or_generate_raises_on_symlink(tmp_path: Path) -> None:
    """A symlink at the token path causes an OSError — not a silent write-through."""
    path = token_path(tmp_path)
    target = tmp_path / "other-file"
    target.write_text("notatoken")
    path.symlink_to(target)

    # The write is refused because _open_private_fd rejects symlinks.
    with pytest.raises(OSError):
        load_or_generate_local_api_token(tmp_path)

    # The symlink target must not have been overwritten with the token.
    assert target.read_text() == "notatoken"
