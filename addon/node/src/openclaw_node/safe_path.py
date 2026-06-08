"""Path-safety helpers for read-only filesystem commands.

Defines the set of filesystem roots the node is permitted to read from and
provides :func:`resolve_safe`, which resolves a caller-supplied path through
symlinks and asserts it remains inside one of those roots.

The allowed roots differ by run mode:

- Add-on mode: the canonical Home Assistant mount points
  (``/config``, ``/share``, ``/addons``, ``/ssl``, ``/media``, ``/backup``).
- Standalone mode: an explicit allowlist read from the
  ``OPENCLAW_ALLOWED_ROOTS`` env var (colon-separated). Empty means *no*
  filesystem access; every fs command refuses with ``NO_ALLOWED_ROOTS``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# Canonical add-on roots. Order is irrelevant; membership matters.
# Must match the active `map:` grants in addon/config.yaml exactly —
# anything listed here is authorized at runtime even if not currently
# mounted. Removed mounts (/addons, /ssl, /backup) are deliberately
# absent; re-add together with the manifest grant when a shipped
# feature needs them.
ADDON_ALLOWED_ROOTS: Final[tuple[str, ...]] = (
    "/config",
    "/share",
    "/media",
)


class OutOfBoundsError(Exception):
    """Raised when a path resolves outside every allowed filesystem root.

    Attributes:
        path: The caller-supplied path that was rejected.
        resolved: The resolved (symlink-followed) absolute path, if known.
    """

    def __init__(self, path: str, resolved: str | None = None) -> None:
        """Initialise with the rejected path.

        Args:
            path: The original caller-supplied path string.
            resolved: The fully-resolved absolute path, if resolution
                succeeded.  ``None`` if the path was rejected before
                resolution (e.g. because it was relative).
        """
        super().__init__("Path is outside the allowed roots")
        self.path = path
        self.resolved = resolved


class NoAllowedRootsError(Exception):
    """Raised when no filesystem roots are configured for the node.

    This is the standalone-mode default when ``OPENCLAW_ALLOWED_ROOTS`` is
    unset, and signals that filesystem commands are refused.
    """

    def __init__(self) -> None:
        """Initialise with a fixed message."""
        super().__init__("No filesystem roots are configured for this node")


def allowed_roots(*, addon_mode: bool) -> tuple[Path, ...]:
    """Return the configured allowed filesystem roots for the current mode.

    Args:
        addon_mode: ``True`` when the process is running as a Home Assistant
            add-on; ``False`` for standalone mode.

    Returns:
        Tuple of resolved :class:`pathlib.Path` roots.  Empty in standalone
        mode when no ``OPENCLAW_ALLOWED_ROOTS`` env var is set.

    Example:
        >>> allowed_roots(addon_mode=True)  # doctest: +ELLIPSIS
        (PosixPath('/config'), ...)
    """
    if addon_mode:
        return tuple(Path(r) for r in ADDON_ALLOWED_ROOTS)
    raw = os.environ.get("OPENCLAW_ALLOWED_ROOTS", "")
    if not raw:
        return ()
    return tuple(Path(part).resolve(strict=False) for part in raw.split(":") if part)


def resolve_safe(path: str, roots: tuple[Path, ...]) -> Path:
    """Resolve *path* and assert it is inside one of *roots*.

    The path must be absolute and, after symlink resolution, must equal or
    be nested under one of the configured roots.  This blocks both
    ``..`` traversal and symlinks pointing outside the sandbox.

    Args:
        path: Caller-supplied path. Must start with ``/``.
        roots: Allowed root directories from :func:`allowed_roots`.

    Returns:
        The resolved absolute :class:`pathlib.Path`.

    Raises:
        OutOfBoundsError: If *path* is relative, or its resolved form is
            not inside any of *roots*.
        NoAllowedRootsError: If *roots* is empty.

    Example:
        >>> roots = (Path('/tmp'),)
        >>> p = resolve_safe('/tmp', roots)
        >>> str(p)
        '/tmp'
    """
    if not roots:
        raise NoAllowedRootsError
    if not path or not path.startswith("/"):
        raise OutOfBoundsError(path)
    resolved = Path(path).resolve(strict=False)
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        else:
            return resolved
    raise OutOfBoundsError(path, str(resolved))
