"""Tests for ``openclaw_node.safe_path``."""

from __future__ import annotations

from pathlib import Path

import pytest

from openclaw_node.safe_path import (
    ADDON_ALLOWED_ROOTS,
    NoAllowedRootsError,
    OutOfBoundsError,
    allowed_roots,
    resolve_safe,
)


def test_addon_allowed_roots_are_ha_mounts() -> None:
    # Must match `map:` in addon/config.yaml exactly. ssl/addons/backup
    # were removed for least-privilege; re-add together when a shipped
    # feature needs them.
    assert set(ADDON_ALLOWED_ROOTS) == {"/config", "/share", "/media"}


def test_allowed_roots_addon_mode() -> None:
    roots = allowed_roots(addon_mode=True)
    assert Path("/config") in roots
    assert len(roots) == len(ADDON_ALLOWED_ROOTS)


def test_allowed_roots_standalone_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ALLOWED_ROOTS", raising=False)
    assert allowed_roots(addon_mode=False) == ()


def test_allowed_roots_standalone_explicit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENCLAW_ALLOWED_ROOTS", f"{tmp_path}:/var/tmp")
    roots = allowed_roots(addon_mode=False)
    assert tmp_path.resolve() in roots
    assert Path("/var/tmp").resolve() in roots


def test_resolve_safe_inside_root(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert resolve_safe(str(f), (tmp_path,)) == f.resolve()


def test_resolve_safe_no_roots_raises() -> None:
    with pytest.raises(NoAllowedRootsError):
        resolve_safe("/anything", ())


def test_resolve_safe_relative_rejected(tmp_path: Path) -> None:
    with pytest.raises(OutOfBoundsError):
        resolve_safe("relative/path", (tmp_path,))


def test_resolve_safe_empty_rejected(tmp_path: Path) -> None:
    with pytest.raises(OutOfBoundsError):
        resolve_safe("", (tmp_path,))


def test_resolve_safe_outside_root(tmp_path: Path) -> None:
    with pytest.raises(OutOfBoundsError) as exc:
        resolve_safe("/etc/passwd", (tmp_path,))
    assert exc.value.path == "/etc/passwd"
    assert exc.value.resolved is not None


def test_resolve_safe_blocks_dotdot(tmp_path: Path) -> None:
    # tmp_path/sub/../../etc resolves to /etc, which is outside tmp_path.
    sub = tmp_path / "sub"
    sub.mkdir()
    target = f"{sub}/../../etc"
    with pytest.raises(OutOfBoundsError):
        resolve_safe(target, (tmp_path,))


def test_resolve_safe_blocks_escape_symlink(tmp_path: Path) -> None:
    link = tmp_path / "escape"
    link.symlink_to("/etc")
    with pytest.raises(OutOfBoundsError):
        resolve_safe(str(link), (tmp_path,))


def test_resolve_safe_allows_internal_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("hi")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    assert resolve_safe(str(link), (tmp_path,)) == target.resolve()


def test_out_of_bounds_error_attrs() -> None:
    err = OutOfBoundsError("/x", "/y")
    assert err.path == "/x"
    assert err.resolved == "/y"
    assert str(err) == "Path is outside the allowed roots"
    assert "/y" not in str(err)


def test_no_allowed_roots_error_message() -> None:
    err = NoAllowedRootsError()
    assert "No filesystem roots" in str(err)
