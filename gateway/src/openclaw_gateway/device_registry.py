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
import json
import logging
import secrets
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

_LOG: Final[logging.Logger] = logging.getLogger(__name__)


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
    """Thread-safe device registry with optional JSON persistence."""

    def __init__(self, persist_path: Path | None = None) -> None:
        """Initialise the registry; load existing entries if a path is given.

        Args:
            persist_path: Optional path to a JSON file. If set, the registry
                loads entries on construction and writes them after every
                mutation.
        """
        self._by_id: dict[str, DeviceRecord] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path
        if self._persist_path is not None:
            self._load()

    def _load(self) -> None:
        """Load entries from the persist file if it exists."""
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text())
        except (OSError, ValueError) as exc:
            _LOG.warning("Failed to load device registry from %s: %s", self._persist_path, exc)
            return
        for entry in raw.get("devices", []):
            try:
                rec = DeviceRecord(
                    device_id=str(entry["device_id"]),
                    public_key_b64url=str(entry["public_key_b64url"]),
                    state=DeviceState(str(entry["state"])),
                    token=str(entry.get("token", "")),
                )
            except (KeyError, ValueError) as exc:
                _LOG.warning("Skipping malformed registry entry: %s", exc)
                continue
            self._by_id[rec.device_id] = rec

    def _save_locked(self) -> None:
        """Write the registry to the persist file. Caller holds _lock."""
        if self._persist_path is None:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "devices": [{**asdict(rec), "state": rec.state.value} for rec in self._by_id.values()]
        }
        tmp = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._persist_path)

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
            self._save_locked()
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
            self._save_locked()
            return record

    def revoke(self, device_id: str) -> None:
        """Remove a device entirely from the registry."""
        with self._lock:
            self._by_id.pop(device_id, None)
            self._save_locked()

    def all_devices(self) -> list[DeviceRecord]:
        """Return a snapshot list of all known devices."""
        with self._lock:
            return list(self._by_id.values())
