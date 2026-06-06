"""Ping command handler.

Responds to the ``ping`` command sent by the gateway with a pong payload
containing an optional echoed message and a server-side timestamp.
"""

from __future__ import annotations

import time
from typing import Any


def handle_ping(params: dict[str, Any]) -> dict[str, Any]:
    """Handle a ``ping`` command invocation from the gateway.

    Returns a pong payload with a monotonic server timestamp (ms since epoch)
    and the optional message echoed back verbatim.

    Args:
        params: Command parameters dict.  The only recognised key is
            ``"message"`` (optional ``str``).

    Returns:
        A dict with keys:
        - ``pong`` (bool): Always ``True``.
        - ``message`` (str): The echoed *message* from *params*, or an empty
          string if none was provided.
        - ``ts`` (int): Current Unix time in milliseconds.

    Example:
        >>> result = handle_ping({"message": "hello"})
        >>> result["pong"]
        True
        >>> result["message"]
        'hello'
        >>> isinstance(result["ts"], int)
        True
    """
    message: str = str(params.get("message", ""))
    return {
        "pong": True,
        "message": message,
        "ts": int(time.time() * 1000),
    }
