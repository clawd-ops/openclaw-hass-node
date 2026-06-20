"""Tests for gateway pairing state."""

from __future__ import annotations

import pytest

from openclaw_node.pairing import PairingError, PairingMachine, PairingState


def test_pairing_success() -> None:
    """Successful connect responses mark the node paired."""
    machine = PairingMachine()
    machine.on_connect_response(ok=True, payload={"sessionId": "s1"})

    assert machine.state is PairingState.PAIRED
    assert machine.is_paired is True


def test_pairing_required_is_pending() -> None:
    """PAIRING_REQUIRED is not fatal."""
    machine = PairingMachine()
    machine.on_connect_response(ok=False, error="PAIRING_REQUIRED")

    assert machine.state is PairingState.PENDING
    assert machine.is_pending is True


def test_fatal_pairing_error() -> None:
    """Other gateway errors raise and move to ERROR."""
    machine = PairingMachine()

    with pytest.raises(PairingError):
        machine.on_connect_response(ok=False, payload={"detail": "bad"}, error="AUTH_FAILED")

    assert machine.state is PairingState.ERROR


def test_fatal_pairing_error_preserves_retry_after_ms() -> None:
    """Gateway retry metadata stays attached to fatal pairing errors."""
    machine = PairingMachine()

    with pytest.raises(PairingError) as exc_info:
        machine.on_connect_response(
            ok=False,
            error="UNAVAILABLE",
            error_message="gateway starting; retry shortly",
            retry_after_ms=500,
        )

    assert exc_info.value.retry_after_ms == 500


def test_reconnect_resets_non_error() -> None:
    """Reconnect resets pending state but preserves fatal errors."""
    machine = PairingMachine()
    machine.on_connect_response(ok=False, error="PAIRING_REQUIRED")
    machine.on_reconnect()
    assert machine.state is PairingState.UNKNOWN

    with pytest.raises(PairingError):
        machine.on_connect_response(ok=False, error="AUTH_FAILED")
    machine.on_reconnect()
    # mypy narrows state from prior asserts; state actually mutated by on_reconnect
    assert machine.state is PairingState.ERROR  # type: ignore[comparison-overlap]
