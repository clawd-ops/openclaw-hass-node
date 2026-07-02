"""Auto-bootstrap local API token persistence.

When the operator does not supply a ``local_api_token``, the node generates
one on first start, persists it under ``data_dir``, and exposes it via the
unauthenticated ``/v1/bootstrap`` endpoint so the HACS integration can fetch
it automatically during config-flow setup (eliminating the copy-paste step).

If the operator later supplies an explicit token via the add-on options, the
auto-generated file is ignored and the bootstrap endpoint is disabled —
the integration's reconfigure flow handles that case manually.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Final

from openclaw_node.identity import _open_private_fd

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

_TOKEN_FILENAME: Final[str] = "local-api-token"
# 32 random bytes → 43-char URL-safe base64 token (no padding).
_TOKEN_BYTES: Final[int] = 32


def generate_local_api_token() -> str:
    """Return a cryptographically random URL-safe token string.

    Returns:
        43-character URL-safe base64 string derived from 32 random bytes.
    """
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_path(data_dir: Path) -> Path:
    """Return the standard path for the persisted local API token.

    Args:
        data_dir: Node data directory (``NodeConfig.data_dir``).

    Returns:
        Path to ``<data_dir>/local-api-token``.
    """
    return data_dir / _TOKEN_FILENAME


def _write_token(path: Path, token: str) -> None:
    """Write *token* to *path* with mode 0o600, refusing symlinks.

    Uses the same hardened write path as the identity module: O_NOFOLLOW
    prevents a symlink at *path* from redirecting the write outside the
    data directory.

    Args:
        path: Destination path (must not be a symlink).
        token: Token string to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_private_fd(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    except BaseException:
        import contextlib

        with contextlib.suppress(OSError):
            os.close(fd)
        raise


def load_or_generate_local_api_token(data_dir: Path) -> tuple[str, bool]:
    """Load the persisted auto-generated local API token, or create one.

    If a token file exists under *data_dir* it is returned as-is.  If it
    does not exist, a fresh token is generated, written with mode 0o600,
    and returned with ``created=True``.

    Args:
        data_dir: Node data directory (``NodeConfig.data_dir``).

    Returns:
        A ``(token, created)`` tuple where *created* is True when a new
        token was generated rather than loaded from disk.

    Raises:
        OSError: If the token file cannot be read or created.
    """
    path = token_path(data_dir)
    if path.exists() and not path.is_symlink():
        try:
            token = path.read_text(encoding="utf-8").strip()
            if token:
                _LOG.info("Loaded persisted local-api-token from %s", path)
                return token, False
        except OSError as exc:
            _LOG.warning("Could not read %s (%s); generating a new token", path, exc)

    token = generate_local_api_token()
    _write_token(path, token)
    _LOG.info(
        "Generated and persisted auto-bootstrap local-api-token to %s "
        "(mode 0o600). The HACS integration can fetch it via GET /v1/bootstrap "
        "during config-flow setup.",
        path,
    )
    return token, True
