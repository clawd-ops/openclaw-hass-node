"""In-memory device registry for the trial gateway.

Tracks per-device pairing state (PENDING vs PAIRED) and the public key that
first introduced the device. First connect of an unknown device records it
as PENDING and the gateway replies PAIRING_REQUIRED; an operator (or, in a
future PR, the OC gateway) approves the device which flips it to PAIRED
and assigns a token.

This registry is not persisted yet — restart resets pairings. Persistence
lands when the gateway grows real config storage.
"""

from __future__ import annotations

import enum
import secrets
import threading
from dataclasses import dataclass
from typing import Final


class DeviceState(enum.Enum):
    """Pairing state of a device known to the gateway."""

    PENDING = "pending"
    PAIRED = "paired"


@dataclass
class DeviceRecord:
    """A single device's entry in the registry."""

    device_id: str
    public_key_b64url: str
    state: DeviceState
    token: str


_TOKEN_BYTES: Final[int] = 32


class DeviceRegistry:
    """Thread-safe in-memory device registry."""

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._by_id: dict[str, DeviceRecord] = {}
        self._lock = threading.Lock()

    def get(self, device_id: str) -> DeviceRecord | None:
        """Return the record for *device_id*, or None if not present."""
        with self._lock:
            return self._by_id.get(device_id)

    def register_or_get(self, device_id: str, public_key_b64url: str) -> DeviceRecord:
        """Register a new device as PENDING, or return the existing record.

        Args:
            device_id: The device identifier.
            public_key_b64url: The base64url-encoded raw Ed25519 public key.

        Returns:
            The current record, with state=PENDING if newly created.

        Raises:
            ValueError: If an existing device presents a different public key.
        """
        with self._lock:
            existing = self._by_id.get(device_id)
            if existing is not None:
                if existing.public_key_b64url != public_key_b64url:
                    msg = f"device {device_id!r} re-registered with different public key"
                    raise ValueError(msg)
                return existing
            record = DeviceRecord(
                device_id=device_id,
                public_key_b64url=public_key_b64url,
                state=DeviceState.PENDING,
                token="",
            )
            self._by_id[device_id] = record
            return record

    def approve(self, device_id: str) -> DeviceRecord:
        """Approve a pending device and assign it a token.

        Args:
            device_id: The device to approve.

        Returns:
            The updated record with state=PAIRED.

        Raises:
            KeyError: If the device is not registered.
        """
        with self._lock:
            record = self._by_id[device_id]
            record.state = DeviceState.PAIRED
            if not record.token:
                record.token = secrets.token_urlsafe(_TOKEN_BYTES)
            return record

    def revoke(self, device_id: str) -> None:
        """Remove a device entirely from the registry."""
        with self._lock:
            self._by_id.pop(device_id, None)

    def all_devices(self) -> list[DeviceRecord]:
        """Return a snapshot list of all known devices."""
        with self._lock:
            return list(self._by_id.values())
