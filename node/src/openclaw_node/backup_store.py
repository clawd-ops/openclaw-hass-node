"""Per-file content-addressed backup store (P3.2 foundation).

Captures prior bytes of any file the node is about to mutate, so every
applied proposal is file-level reversible. Lives outside ``/config`` and
survives add-on rebuilds.

Layout under :attr:`BackupStore.root`::

    objects/<sha256[0:2]>/<sha256>   raw prior bytes, deduplicated
    index/<url-encoded-path>.jsonl   append-only version log per file
    meta.json                        store version, configured cap
    tmp/                             in-progress writes, fsync-then-rename

This module is pure storage: ``capture``, ``history``, ``fetch_object``,
``resolve_version``, and ``diff``. Eviction/GC and pinning ship in a
follow-up PR. Dispatcher wiring (``fs.write``, ``fs.restore``) ships in
P3.2.2.

Concurrency model
-----------------
``BackupStore`` is **single-writer**: at most one call to :meth:`capture` may
be in flight at a time. The OpenClaw node dispatches commands sequentially in
its event loop, which enforces this invariant. The index ``O_APPEND`` write is
atomic at the OS level (a single ``write(2)`` call), but the read-modify-write
in ``_last_sha`` → ``_atomic_append_line`` is not safe under concurrent
writers; adding locking is deferred until the concurrency model changes.

Orphan objects
--------------
If the object write succeeds but the subsequent index append fails, the object
body is stored but not referenced by any index line. Such orphan objects are
harmless (the content is intact) and will be reclaimed by the GC pass
introduced in the eviction PR. No attempt is made to roll back a partially
committed capture.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as _dt
import difflib
import hashlib
import io
import json
import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Final, Literal

_STORE_VERSION: Final[int] = 1
_DEFAULT_CAP_BYTES: Final[int] = 500 * 1024 * 1024
# 250 encoded chars + ".jsonl" (6) = 256, safely below the 255-byte ext4 limit.
# Each raw "/" expands to "%2F" (3 chars), so 250 encoded supports ~60-80 raw chars
# of path depth — adequate for the deepest realistic HA custom_components paths.
_INDEX_NAME_MAX: Final[int] = 250

Op = Literal["write", "delete", "move-src", "move-dst", "restore"]


class BackupStoreError(Exception):
    """Raised on store integrity or layout errors."""


class VersionNotFoundError(BackupStoreError):
    """Raised when a requested version selector matches no recorded version."""


class ObjectMissingError(BackupStoreError):
    """Raised when an index line references an object that is no longer present."""


class _CorruptMetaError(BackupStoreError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"Corrupt meta.json: {detail}")


class _UnsupportedStoreVersionError(BackupStoreError):
    def __init__(self, meta: object) -> None:
        super().__init__(f"Unsupported store_version in meta.json: {meta!r}")


class _CorruptIndexLineError(BackupStoreError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"Corrupt index line: {detail}")


class _IndexLineNotObjectError(BackupStoreError):
    def __init__(self) -> None:
        super().__init__("Index line is not a JSON object")


class _IndexLineMissingFieldError(BackupStoreError):
    def __init__(self, field: object) -> None:
        super().__init__(f"Index line missing field: {field}")


class _UnknownOpError(BackupStoreError):
    def __init__(self, op: str) -> None:
        super().__init__(f"Unknown op: {op!r}")


class _EncodedPathTooLongError(BackupStoreError):
    def __init__(self, path: str) -> None:
        super().__init__(
            f"Encoded path too long for index filename (>{_INDEX_NAME_MAX} chars): {path!r}",
        )


class _ObjectNotPresentError(ObjectMissingError):
    def __init__(self, sha256: str) -> None:
        super().__init__(f"Object {sha256} is not present")


class _ObjectEvictedError(ObjectMissingError):
    def __init__(self, sha256: str, path: str) -> None:
        super().__init__(f"Version {sha256} for {path!r} was evicted; cannot diff")


class _NoVersionsForPathError(VersionNotFoundError):
    def __init__(self, path: str) -> None:
        super().__init__(f"No versions recorded for {path!r}")


class _VersionOutOfRangeError(VersionNotFoundError):
    def __init__(self, version: int, path: str) -> None:
        super().__init__(f"Version {version} out of range for {path!r}")


class _NoSuchProposalError(VersionNotFoundError):
    def __init__(self, proposal_id: str) -> None:
        super().__init__(f"No version with proposal_id={proposal_id!r}")


class _NoVersionAtTimeError(VersionNotFoundError):
    def __init__(self, at: str, path: str) -> None:
        super().__init__(f"No version at or before {at!r} for {path!r}")


class _NoVersionWithShaError(VersionNotFoundError):
    def __init__(self, sha256: str, path: str) -> None:
        super().__init__(f"No version with sha256={sha256!r} for {path!r}")


class _LiveReadError(BackupStoreError):
    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"Cannot read live bytes for {path!r}: {detail}")


class _SelectorRequiredError(ValueError):
    def __init__(self) -> None:
        super().__init__("Exactly one of version/proposal_id/at is required")


@dataclasses.dataclass(frozen=True)
class Version:
    """A single recorded version of a tracked path.

    Attributes:
        ts: ISO-8601 UTC timestamp the capture was recorded.
        proposal_id: Identifier of the agent-bridge proposal that drove the change.
        sha256: Hex digest of the prior bytes (the object key).
        size: Size of the prior bytes in bytes.
        op: One of ``write``, ``delete``, ``move-src``, ``move-dst``, ``restore``.
        actor: Caller identity recorded on the line.
        prev_sha256: Hex digest of the version before this one, or ``None`` at first capture.
        evicted: ``True`` when the object body has been GC'd but metadata is kept.
    """

    ts: str
    proposal_id: str
    sha256: str
    size: int
    op: Op
    actor: str
    prev_sha256: str | None
    evicted: bool = False

    def to_json(self) -> str:
        """Serialize this version to a single JSONL line (no trailing newline).

        Returns:
            Compact JSON encoding suitable for appending to ``index/<path>.jsonl``.
        """
        payload: dict[str, object] = {
            "ts": self.ts,
            "proposal_id": self.proposal_id,
            "sha256": self.sha256,
            "size": self.size,
            "op": self.op,
            "actor": self.actor,
            "prev_sha256": self.prev_sha256,
        }
        if self.evicted:
            payload["evicted"] = True
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> Version:
        """Parse one JSONL line into a :class:`Version`.

        Args:
            line: A single JSONL line (with or without a trailing newline).

        Returns:
            The decoded version.

        Raises:
            BackupStoreError: If the line is not valid JSON or is missing
                required fields.
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _CorruptIndexLineError(str(exc)) from exc
        if not isinstance(data, dict):
            raise _IndexLineNotObjectError
        try:
            return cls(
                ts=str(data["ts"]),
                proposal_id=str(data["proposal_id"]),
                sha256=str(data["sha256"]),
                size=int(data["size"]),
                op=_validate_op(str(data["op"])),
                actor=str(data["actor"]),
                prev_sha256=(
                    None if data.get("prev_sha256") is None else str(data["prev_sha256"])
                ),
                evicted=bool(data.get("evicted", False)),
            )
        except KeyError as exc:
            raise _IndexLineMissingFieldError(exc.args[0]) from exc
        except (ValueError, TypeError) as exc:
            raise _CorruptIndexLineError(str(exc)) from exc


def _validate_op(value: str) -> Op:
    """Narrow a free-form string into the :class:`Op` literal type.

    Args:
        value: Operation string read from JSON.

    Returns:
        The validated operation.

    Raises:
        BackupStoreError: If *value* is not a known op.
    """
    if value not in ("write", "delete", "move-src", "move-dst", "restore"):
        raise _UnknownOpError(value)
    return value  # type: ignore[return-value]


def _utc_now_iso() -> str:
    """Return the current UTC time as ISO-8601 with second precision and ``Z`` suffix."""
    return _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> _dt.datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime.

    Accepts both ``Z``-suffix (canonical store format) and explicit UTC offsets
    such as ``+00:00``.

    Args:
        ts: ISO-8601 timestamp string.

    Returns:
        Timezone-aware :class:`datetime.datetime` in UTC.
    """
    return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _encode_path(path: str) -> str:
    """Encode an absolute path into a single safe filename component.

    Args:
        path: Caller-supplied absolute path.

    Returns:
        ``urllib.parse.quote(path, safe='')`` with no ``/`` segments.

    Raises:
        BackupStoreError: If the encoded form exceeds the file-name length cap.

    Note:
        Assumes a case-sensitive filesystem (Linux ext4/overlayfs). On
        case-insensitive mounts (macOS, some SMB shares) two paths that differ
        only in case would produce the same index file. HASS runs on Linux so
        this is not a concern in production.
    """
    encoded = urllib.parse.quote(path, safe="")
    if len(encoded) > _INDEX_NAME_MAX:
        raise _EncodedPathTooLongError(path)
    return encoded


def _fsync_dir(path: Path) -> None:
    """Open *path* as a directory and fsync it to flush directory-entry changes.

    Required after ``os.replace`` to make the rename crash-durable on ext4 and
    similar journalling filesystems. Suppressed silently on platforms that do
    not support fsyncing directories (e.g. Windows in tests).
    """
    with contextlib.suppress(OSError):
        dir_fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _atomic_write_bytes(target: Path, data: bytes, tmp_dir: Path) -> None:
    """Write *data* to *target* via ``tmp/``-staged fsync-then-rename.

    The sequence is: write → fsync file → rename → fsync parent dir.
    Fsyncing the parent directory after the rename makes the rename itself
    crash-durable on ext4 and similar journalling filesystems; without it a
    crash immediately after ``replace`` may leave the directory entry unwritten
    while the object data is already on disk.

    Args:
        target: Destination path; created or replaced atomically.
        data: Bytes to write.
        tmp_dir: Directory under the store root used for staging.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="bs.", dir=str(tmp_dir))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    _fsync_dir(target.parent)


def _atomic_append_line(target: Path, line: str) -> None:
    """Append a single line to *target* with ``O_APPEND`` and ``fsync``.

    Args:
        target: Destination JSONL file; created if missing.
        line: Line contents (the function adds the trailing newline).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


class BackupStore:
    """Per-file content-addressed backup store.

    Constructed against a root directory under ``/share`` (or any path in tests).
    The constructor is non-destructive: it creates the layout if missing but
    leaves an existing store untouched.

    Args:
        root: Directory that holds ``objects/``, ``index/``, ``tmp/``, ``meta.json``.
        cap_bytes: Configured cap for total store size (used by GC in a follow-up).

    Example:
        >>> store = BackupStore(Path("/tmp/store"))  # doctest: +SKIP
        >>> store.capture("/config/configuration.yaml", b"old contents",
        ...               proposal_id="abc", op="write")  # doctest: +SKIP
        Version(...)
    """

    def __init__(self, root: Path, *, cap_bytes: int = _DEFAULT_CAP_BYTES) -> None:
        """Initialize the store, creating the directory layout if absent.

        Args:
            root: Root directory for the store.
            cap_bytes: Configured cap (persisted to ``meta.json`` on creation).

        Raises:
            BackupStoreError: If ``meta.json`` exists but does not parse, or
                if its ``store_version`` is unrecognised.
        """
        self.root: Final[Path] = root
        self.cap_bytes: Final[int] = cap_bytes
        self._objects_dir: Final[Path] = root / "objects"
        self._index_dir: Final[Path] = root / "index"
        self._tmp_dir: Final[Path] = root / "tmp"
        self._meta_path: Final[Path] = root / "meta.json"
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        """Create the on-disk layout if missing; validate ``meta.json`` if present."""
        self.root.mkdir(parents=True, exist_ok=True)
        self._objects_dir.mkdir(parents=True, exist_ok=True)
        self._index_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        if self._meta_path.exists():
            try:
                meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise _CorruptMetaError(str(exc)) from exc
            if not isinstance(meta, dict) or meta.get("store_version") != _STORE_VERSION:
                raise _UnsupportedStoreVersionError(meta)
        else:
            _atomic_write_bytes(
                self._meta_path,
                json.dumps(
                    {"store_version": _STORE_VERSION, "cap_bytes": self.cap_bytes},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8"),
                self._tmp_dir,
            )

    def _object_path(self, sha256: str) -> Path:
        """Return the on-disk location of an object body.

        Args:
            sha256: Hex digest of the object.

        Returns:
            Path under ``objects/<sha[0:2]>/<sha>``.
        """
        return self._objects_dir / sha256[:2] / sha256

    def _index_path(self, path: str) -> Path:
        """Return the index file for *path*.

        Args:
            path: Caller-supplied absolute path.

        Returns:
            Path under ``index/<url-encoded-path>.jsonl``.
        """
        return self._index_dir / f"{_encode_path(path)}.jsonl"

    def capture(
        self,
        path: str,
        prior_bytes: bytes,
        *,
        proposal_id: str,
        op: Op,
        actor: str = "clawd",
    ) -> Version:
        """Record one new version for *path*.

        Atomically writes the object body (if not already present) and
        appends a JSONL line to the per-path index. Safe to call before the
        live mutation runs.

        Args:
            path: Caller-supplied absolute path being mutated.
            prior_bytes: Bytes to capture (the current contents of *path*,
                or empty for a fresh-create no-op caller).
            proposal_id: Agent-bridge proposal identifier.
            op: The operation that motivated this capture.
            actor: Identity recorded on the version line.

        Returns:
            The newly written :class:`Version`.
        """
        sha = hashlib.sha256(prior_bytes).hexdigest()
        obj = self._object_path(sha)
        if not obj.exists():
            obj.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_bytes(obj, prior_bytes, self._tmp_dir)
        prev_sha = self._last_sha(path)
        version = Version(
            ts=_utc_now_iso(),
            proposal_id=proposal_id,
            sha256=sha,
            size=len(prior_bytes),
            op=op,
            actor=actor,
            prev_sha256=prev_sha,
        )
        _atomic_append_line(self._index_path(path), version.to_json())
        return version

    def _last_sha(self, path: str) -> str | None:
        """Return the sha256 of the most recent version of *path*, if any.

        Args:
            path: Caller-supplied absolute path.

        Returns:
            Hex digest of the most recent recorded version, or ``None`` if
            *path* has no history.
        """
        try:
            versions = self.history(path)
        except BackupStoreError:
            return None
        return versions[-1].sha256 if versions else None

    def history(self, path: str) -> list[Version]:
        """Return all recorded versions of *path* in insertion order.

        Args:
            path: Caller-supplied absolute path.

        Returns:
            Empty list if *path* has no recorded versions; otherwise the
            parsed version log in chronological order.

        Raises:
            BackupStoreError: If the index file exists but contains a
                corrupt line.
        """
        idx = self._index_path(path)
        if not idx.exists():
            return []
        with idx.open("r", encoding="utf-8") as handle:
            return [Version.from_json(line) for line in handle if line.strip()]

    def fetch_object(self, sha256: str) -> bytes:
        """Read object bytes by hash.

        Args:
            sha256: Hex digest of the object.

        Returns:
            Raw object bytes.

        Raises:
            ObjectMissingError: If the object is not present (evicted or
                never written).
        """
        obj = self._object_path(sha256)
        if not obj.exists():
            raise _ObjectNotPresentError(sha256)
        return obj.read_bytes()

    def resolve_version(
        self,
        path: str,
        *,
        version: int | None = None,
        proposal_id: str | None = None,
        at: str | None = None,
    ) -> Version:
        """Resolve one of several selectors to a concrete :class:`Version`.

        Exactly one of *version*, *proposal_id*, or *at* must be supplied.

        Args:
            path: Caller-supplied absolute path.
            version: 1-indexed position in :meth:`history` (``-1`` selects
                the most recent).
            proposal_id: Match a recorded proposal identifier.
            at: ISO-8601 timestamp; selects the latest version with
                ``ts <= at``.

        Returns:
            The matching :class:`Version`.

        Raises:
            ValueError: If zero or multiple selectors are supplied.
            VersionNotFoundError: If no recorded version matches.
        """
        chosen = [s for s in (version, proposal_id, at) if s is not None]
        if len(chosen) != 1:
            raise _SelectorRequiredError
        versions = self.history(path)
        if not versions:
            raise _NoVersionsForPathError(path)
        if version is not None:
            try:
                return versions[version - 1] if version > 0 else versions[version]
            except IndexError as exc:
                raise _VersionOutOfRangeError(version, path) from exc
        if proposal_id is not None:
            for v in versions:
                if v.proposal_id == proposal_id:
                    return v
            raise _NoSuchProposalError(proposal_id)
        if at is None:  # pragma: no cover - guarded by selector check above
            raise _SelectorRequiredError
        at_dt = _parse_iso(at)
        candidates = [v for v in versions if _parse_iso(v.ts) <= at_dt]
        if not candidates:
            raise _NoVersionAtTimeError(at, path)
        return candidates[-1]

    def diff(
        self,
        path: str,
        *,
        from_version: int | str,
        to_version: int | str | None,
    ) -> str:
        """Produce a unified diff between two versions of *path*.

        Args:
            path: Caller-supplied absolute path.
            from_version: 1-indexed position or recorded sha256.
            to_version: 1-indexed position, recorded sha256, or ``None`` to
                diff *from_version* against the current live bytes on disk.

        Returns:
            Unified diff text (may be empty if the two sides are identical).

        Raises:
            BackupStoreError: If a side cannot be located.
        """
        from_bytes, from_label = self._resolve_side(path, from_version)
        if to_version is None:
            live = Path(path)
            try:
                to_bytes = live.read_bytes()
            except OSError as exc:
                raise _LiveReadError(path, str(exc)) from exc
            to_label = f"{path}:current"
        else:
            to_bytes, to_label = self._resolve_side(path, to_version)
        from_text = _decode_for_diff(from_bytes)
        to_text = _decode_for_diff(to_bytes)
        return "".join(
            difflib.unified_diff(
                from_text.splitlines(keepends=True),
                to_text.splitlines(keepends=True),
                fromfile=from_label,
                tofile=to_label,
            )
        )

    def _resolve_side(self, path: str, selector: int | str) -> tuple[bytes, str]:
        """Resolve one side of a diff to bytes and a display label.

        Args:
            path: Caller-supplied absolute path.
            selector: 1-indexed version position or recorded sha256.

        Returns:
            ``(bytes, label)`` where label is suitable for ``unified_diff``.

        Raises:
            BackupStoreError: If the selector resolves to no recorded version
                or to an evicted object.
        """
        if isinstance(selector, int):
            v = self.resolve_version(path, version=selector)
        else:
            versions = [v for v in self.history(path) if v.sha256 == selector]
            if not versions:
                raise _NoVersionWithShaError(selector, path)
            v = versions[-1]
        if v.evicted:
            raise _ObjectEvictedError(v.sha256, path)
        return self.fetch_object(v.sha256), f"{path}@{v.ts}"


def _decode_for_diff(data: bytes) -> str:
    """Best-effort decode for unified-diff input.

    Args:
        data: Raw bytes.

    Returns:
        UTF-8 decoded text where possible; falls back to Latin-1 so binary
        content still produces a stable diff without raising.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return io.TextIOWrapper(io.BytesIO(data), encoding="latin-1").read()
