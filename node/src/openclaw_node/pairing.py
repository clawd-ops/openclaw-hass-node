"""Pairing state machine for the OpenClaw gateway WS connection.

Tracks whether the node is unpaired (awaiting user approval on the gateway),
paired (has a valid device token), or in an error state.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Final

_LOG: Final[logging.Logger] = logging.getLogger(__name__)


class PairingState(Enum):
    """Lifecycle state of the gateway pairing relationship.

    Attributes:
        UNKNOWN: Initial state before the first connect attempt.
        PENDING: Gateway returned PAIRING_REQUIRED; awaiting user approval.
        PAIRED: Gateway accepted the connection; node is operational.
        ERROR: An unrecoverable error occurred during pairing.
    """

    UNKNOWN = auto()
    PENDING = auto()
    PAIRED = auto()
    ERROR = auto()


class PairingError(Exception):
    """Raised when the gateway rejects a connect request with a fatal error.

    Attributes:
        code: The error code string from the gateway response.
        message: Human-readable error description.
    """

    def __init__(self, code: str, message: str) -> None:
        """Initialise with a gateway error code and message.

        Args:
            code: Machine-readable error code (e.g. ``"AUTH_FAILED"``).
            message: Human-readable description of the error.
        """
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class PairingMachine:
    """State machine that tracks the node's pairing status with the gateway.

    Callers drive transitions by calling :meth:`on_connect_response` after
    each gateway ``connect`` response.  The current state is readable via
    :attr:`state`.

    Example:
        >>> machine = PairingMachine()
        >>> machine.state
        <PairingState.UNKNOWN: 1>
        >>> machine.on_connect_response(ok=True, payload={"sessionId": "s1"})
        >>> machine.state
        <PairingState.PAIRED: 3>
    """

    def __init__(self) -> None:
        """Initialise the machine in the UNKNOWN state."""
        self._state = PairingState.UNKNOWN
        self._pending_request_id: str | None = None

    @property
    def state(self) -> PairingState:
        """Return the current pairing state.

        Returns:
            The current :class:`PairingState`.
        """
        return self._state

    @property
    def is_paired(self) -> bool:
        """Return True when the node has an active paired session.

        Returns:
            True if :attr:`state` is :attr:`PairingState.PAIRED`.
        """
        return self._state is PairingState.PAIRED

    @property
    def is_pending(self) -> bool:
        """Return True when pairing approval is outstanding.

        Returns:
            True if :attr:`state` is :attr:`PairingState.PENDING`.
        """
        return self._state is PairingState.PENDING

    def on_connect_response(
        self,
        ok: bool,
        payload: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        """Transition state based on a gateway ``connect`` response.

        Args:
            ok: True if the gateway accepted the connection.
            payload: Response payload dict when *ok* is True.
            error: Error code string when *ok* is False.

        Raises:
            PairingError: If *ok* is False and the error is not
                ``"PAIRING_REQUIRED"`` (i.e. a fatal auth failure).
        """
        if ok:
            _LOG.info("Gateway accepted connection; node is paired.")
            self._state = PairingState.PAIRED
            return

        code = error or "UNKNOWN_ERROR"
        if code == "PAIRING_REQUIRED":
            _LOG.info(
                "Gateway returned PAIRING_REQUIRED — awaiting user approval on the gateway."
            )
            self._state = PairingState.PENDING
            return

        _LOG.error("Gateway rejected connection with code=%s", code)
        self._state = PairingState.ERROR
        raise PairingError(code=code, message=str(payload or ""))

    def on_reconnect(self) -> None:
        """Reset state to UNKNOWN when the WS connection drops.

        This allows the machine to re-run the handshake on the next connection
        attempt without carrying stale state.
        """
        if self._state is not PairingState.ERROR:
            _LOG.debug("WS reconnect — resetting pairing state to UNKNOWN.")
            self._state = PairingState.UNKNOWN
