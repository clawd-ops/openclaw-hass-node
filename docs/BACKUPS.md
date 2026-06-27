# Backups — per-file versioning store

> No git in `/config`. No Supervisor snapshot per file change. No
> `.bak` sidecars next to live files. Supervisor snapshots are only
> taken when the user explicitly asks for one, or on a user-defined
> cadence.

## Goals

- Every applied proposal that mutates a protected-root file is
  reversible at the file level.
- Storage cost is bounded and predictable.
- Restores are themselves proposal-gated and auditable.
- The store survives add-on (app) rebuilds (lives in `/share`, included in
  normal HA backups).

## Store layout

```
/share/openclaw-backups/
├── objects/
│   └── <sha[0:2]>/<sha>          # raw prior bytes, content-addressed
├── index/
│   └── <url-encoded-path>.jsonl  # one line per version, append-only
├── meta.json                     # store version, cap, retention policy
└── tmp/                          # in-progress writes, fsync-then-rename
```

Content addressing means identical content across versions or files
stores once. `index/<path>.jsonl` is the per-file history. Listing
versions for a path is one file read.

## Index line format

```json
{
  "ts": "2026-06-05T23:14:59Z",
  "proposal_id": "ab12cd34",
  "sha256": "8c2b...",
  "size": 4096,
  "op": "write",
  "actor": "clawd",
  "prev_sha256": "1a4f..."
}
```

`op` is one of `write`, `delete`, `move-src`, `move-dst`, `restore`.
For `delete`, the line records the prior bytes; the object is kept
until evicted.

## Write path

1. Proposal accepted by user.
2. Node reads current bytes of target file (if exists), computes
   sha256.
3. If `objects/<sha>` doesn't exist, atomic-write it (`tmp/` →
   `fsync` → `rename`).
4. Append a line to `index/<path>.jsonl` (also atomic via O_APPEND on
   a single-writer process; node is a single process).
5. Apply the proposal's mutation to the live file.
6. If step 5 fails, restore live file from the just-captured object
   and surface the error.

## Restore path

- Command: `fs.restore path=<p>` with optional `at`, `proposal`, or
  `version` selector.
- Restore is itself a write → produces a fresh proposal that the user
  must accept.
- The restore proposal's body shows: target path, source version
  (ts + proposal_id), sha mismatch with current bytes, and a diff.

## Listing and diffing

- `fs.history path=<p>` → reads `index/<p>.jsonl`, returns the
  version list.
- `fs.diff path=<p> from=<vA> to=<vB>` → fetches both objects,
  produces a unified diff. `from`/`to` accept `version:N`, `ts:...`,
  `proposal:<id>`, or `current`.

## Retention and eviction

- Default cap: 500 MB total under `/share/openclaw-backups/`.
  Configurable via `meta.json`.
- Eviction policy: LRU on the *object* layer, by last-referenced ts in
  any index. When evicting an object, every index line that points at
  it is rewritten with `evicted: true` (object body gone, metadata
  kept).
- Per-path pin: `fs.pin path=<p> keep_last=<N>` keeps the last N
  versions of that path off the eviction list.

## What is NOT backed up here

- Reads. Only mutations.
- Files outside protected roots, unless explicitly opted in via
  `fs.backup path=<p>` (one-shot capture).
- `.storage/`, `home-assistant_v2.db`, log files. These are excluded
  from `fs.*` writes by the `.storage` HARD rule and by an ignore list
  in the backup engine.

## Supervisor snapshots — explicit only

Supervisor partial/full snapshots are coarse, expensive, and not the
right grain for normal config edits. The node will trigger them only
when:

- The user explicitly requests a Supervisor snapshot (no dedicated
  `ha.supervisor.snapshots.*` command is registered today; the path
  for this is proposal-gated and not yet shipped).
- The user has configured a snapshot cadence in the node config
  (`backups.supervisor_cadence: daily|weekly|off`).
- A proposal explicitly marks `request_supervisor_snapshot: true`
  (e.g. a large multi-file refactor) and the user accepts.

There is no per-file-change Supervisor snapshot. Ever.

## Disaster recovery

- Store is included in normal HA full backups by virtue of being
  under `/share`. Restoring HA from a snapshot restores the backup
  store too.
- If `/share/openclaw-backups/` is missing or corrupt at startup, the
  node refuses mutating commands until the user resolves it
  (`fs.backup_init` to start fresh, or restore from HA backup).
