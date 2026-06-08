"""Read-only system-introspection command handlers (P3.1).

Currently exposes a single command:

- ``system.which`` - locate an executable basename in ``PATH``.

The mutating ``system.run`` command is intentionally NOT in this module; it
requires the ``operator.admin`` scope and ships in P3.2.
"""

from __future__ import annotations

import os
import shutil
from typing import Any


def handle_system_which(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ``system.which`` - locate an executable in ``PATH``.

    Args:
        params: Command parameters. Recognised keys:

            - ``name`` (str, required): Executable name to look up.

    Returns:
        Dict with ``ok``, ``name``, ``found`` (bool), and on success
        ``path`` (str). When the parameter is missing or is not a basename,
        returns an error dict with code ``NAME_REQUIRED``.

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
    if "/" in name or os.sep in name:
        return {
            "ok": False,
            "error": "NAME_REQUIRED",
            "message": "Executable name must be a basename",
        }
    located = shutil.which(name)
    if located is None:
        return {"ok": True, "name": name, "found": False}
    return {
        "ok": True,
        "name": name,
        "found": True,
        "path": located,
    }
