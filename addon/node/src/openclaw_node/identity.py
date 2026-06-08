"""Ed25519 device identity for the OpenClaw HASS Node.

Generates, persists, and loads the node's Ed25519 keypair.  The identity is
stored as JSON at the path specified by :class:`~openclaw_node.config.NodeConfig`.

Device ID is the SHA-256 hex digest of the raw 32-byte public key.
Public key is base64url-encoded (no padding) of the raw 32 bytes.
Signatures use the v3 pipe-separated payload format.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

_CLIENT_ID: Final[str] = "openclaw-hass-node"
_CLIENT_MODE: Final[str] = "node"
_PLATFORM: Final[str] = "linux"
_DEVICE_FAMILY: Final[str] = "hass-node"


class InvalidIdentityKeyError(TypeError):
    """Raised when a persisted identity key has the wrong key type."""

    def __init__(self) -> None:
        """Initialise with a fixed message."""
        super().__init__("Persisted identity key is not an Ed25519 private key")


def _b64url(data: bytes) -> str:
    """Encode *data* as base64url without padding.

    Args:
        data: Raw bytes to encode.

    Returns:
        Base64url string with ``=`` padding stripped.
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _raw_public_key(pub: Ed25519PublicKey) -> bytes:
    """Extract the raw 32-byte public key from an Ed25519 public key object.

    Args:
        pub: An Ed25519 public key.

    Returns:
        The raw 32-byte public key material.
    """
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _device_id(raw_pub: bytes) -> str:
    """Compute the device ID as the SHA-256 hex digest of the raw public key.

    Args:
        raw_pub: Raw 32-byte Ed25519 public key.

    Returns:
        Lowercase hex SHA-256 digest string (64 chars).
    """
    return hashlib.sha256(raw_pub).hexdigest()


@dataclass(frozen=True)
class DeviceIdentity:
    """Persisted Ed25519 identity for this node instance.

    Attributes:
        device_id: SHA-256 hex digest of the raw 32-byte public key.
        public_key_b64url: Raw 32-byte public key, base64url-encoded (no padding).
        private_key: The Ed25519 private key object (not serialised to callers).
    """

    device_id: str
    public_key_b64url: str
    private_key: Ed25519PrivateKey

    def sign_connect(
        self,
        nonce: str,
        role: str,
        scopes: list[str],
        token: str,
    ) -> tuple[str, int]:
        """Sign the v3 connect payload and return (signature_b64url, signed_at_ms).

        The signed payload is the UTF-8 encoding of:
        ``v3|{deviceId}|{clientId}|{clientMode}|{role}|{scopes_comma}|{signedAtMs}|{token}|{nonce}|{platform}|{deviceFamily}``

        Args:
            nonce: The nonce received from the gateway ``connect.challenge`` event.
            role: The role requested on this connection (e.g. ``"node"``).
            scopes: List of scope strings to request (e.g. ``["operator.write"]``).
            token: The device token (empty string on first pairing).

        Returns:
            A tuple of ``(signature_b64url, signed_at_ms)`` where
            ``signed_at_ms`` is the Unix epoch time in milliseconds at signing
            time and ``signature_b64url`` is the base64url Ed25519 signature
            (no padding).
        """
        signed_at_ms = int(time.time() * 1000)
        scopes_str = ",".join(scopes)
        payload = "|".join(
            [
                "v3",
                self.device_id,
                _CLIENT_ID,
                _CLIENT_MODE,
                role,
                scopes_str,
                str(signed_at_ms),
                token,
                nonce,
                _PLATFORM,
                _DEVICE_FAMILY,
            ]
        )
        sig_bytes = self.private_key.sign(payload.encode("utf-8"))
        return _b64url(sig_bytes), signed_at_ms


def generate_identity() -> DeviceIdentity:
    """Generate a new Ed25519 keypair and return a :class:`DeviceIdentity`.

    Returns:
        A freshly generated :class:`DeviceIdentity`.
    """
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    raw_pub = _raw_public_key(pub)
    return DeviceIdentity(
        device_id=_device_id(raw_pub),
        public_key_b64url=_b64url(raw_pub),
        private_key=priv,
    )


def save_identity(identity: DeviceIdentity, path: Path) -> None:
    """Persist *identity* to *path* as JSON.

    The private key is serialised as PKCS8 PEM (no encryption).  Creates
    parent directories if they do not exist.

    Args:
        identity: The :class:`DeviceIdentity` to persist.
        path: Destination file path (typically ``data_dir/node-key.json``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    priv_pem = identity.private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    data = {
        "device_id": identity.device_id,
        "public_key_b64url": identity.public_key_b64url,
        "private_key_pem": priv_pem,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_identity(path: Path) -> DeviceIdentity:
    """Load a :class:`DeviceIdentity` from *path*.

    Args:
        path: Path to the JSON file written by :func:`save_identity`.

    Returns:
        The loaded :class:`DeviceIdentity`.

    Raises:
        FileNotFoundError: If *path* does not exist.
        KeyError: If the JSON is missing required fields.
        InvalidIdentityKeyError: If the persisted private key is not an
            Ed25519 key.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    priv = load_pem_private_key(raw["private_key_pem"].encode(), password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise InvalidIdentityKeyError
    return DeviceIdentity(
        device_id=raw["device_id"],
        public_key_b64url=raw["public_key_b64url"],
        private_key=priv,
    )


def load_or_generate(path: Path) -> tuple[DeviceIdentity, bool]:
    """Load identity from *path*, generating and saving one if absent.

    Args:
        path: Path to the identity JSON file.

    Returns:
        A tuple ``(identity, created)`` where *created* is True when a new
        identity was generated rather than loaded from disk.
    """
    if path.exists():
        return load_identity(path), False
    identity = generate_identity()
    save_identity(identity, path)
    return identity, True
