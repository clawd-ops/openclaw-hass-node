"""Read-only system-introspection command handlers (P3.1).

Currently exposes a single command:

- ``system.which`` - locate an executable in ``PATH`` and, if found, capture
  the first line of ``<bin> --version`` output (best-effort, 2 s timeout).

The mutating ``system.run`` command is intentionally NOT in this module; it
requires the ``operator.admin`` scope and ships in P3.2.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - version probe only; argv is a single resolved binary path.
from typing import Any, Final

_VERSION_TIMEOUT_S: Final[float] = 2.0


def _probe_version(binary: str) -> str | None:
    """Best-effort capture of the first line of ``<binary> --version``.

    Returns ``None`` on any failure; never raises.

    Args:
        binary: Absolute path to the executable, as returned by
            :func:`shutil.which`.

    Returns:
        First line of stdout (or stderr if stdout is empty), stripped, or
        ``None`` if the probe failed or produced no output.
    """
    try:
        completed = subprocess.run(  # nosec B603 - argv list, no shell, fixed flag.
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = completed.stdout or completed.stderr or ""
    first = output.strip().splitlines()
    if not first:
        return None
    return first[0].strip()


def handle_system_which(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ``system.which`` - locate an executable in ``PATH``.

    Args:
        params: Command parameters. Recognised keys:

            - ``name`` (str, required): Executable name to look up.

    Returns:
        Dict with ``ok``, ``name``, ``found`` (bool), and on success
        ``path`` (str) plus optional ``version`` (str or ``None``). When
        the parameter is missing, returns an error dict with code
        ``NAME_REQUIRED``.

    Example:
        >>> result = handle_system_which({"name": "sh"})
        >>> result["found"] in (True, False)
        True
    """
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return {
            "ok": False,
            "error": "NAME_REQUIRED",
            "message": "Missing required 'name' parameter",
        }
    located = shutil.which(name)
    if located is None:
        return {"ok": True, "name": name, "found": False}
    version = _probe_version(located)
    return {
        "ok": True,
        "name": name,
        "found": True,
        "path": located,
        "version": version,
    }
