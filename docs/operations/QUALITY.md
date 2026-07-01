# Code Quality Standards

> Non-negotiable. Enforced in CI. PRs that fail any of these gates
> cannot merge.

## Language

- **Python 3.13+** for both the node and the `custom_components/`
  shim. Matches HA's runtime and lets the same engineers swap
  between the two without context switching. The conversation-agent
  shim has to be Python anyway (it imports from HA core); aligning
  the add-on (app) rules out an extra build chain.
- `pyproject.toml` for both packages. `src/` layout. `uv` for
  dependency management (fast, reproducible lockfiles).

## Type checking — strict

- **`mypy --strict`** on `node/` and `custom_components/openclaw_hass_node_assist/`.
  No `Any` leaks. No implicit `Optional`. No untyped defs. No
  `# type: ignore` without an inline comment explaining why and a
  ticket link.
- **`pyright`** in `strict` mode as a second-opinion gate
  (different inference engine catches different bugs).
- Public functions must declare full parameter and return types.
- `TypedDict` / `dataclass(frozen=True)` / `pydantic.BaseModel` for
  every wire-shape (gateway WS messages, HA REST/WS responses,
  agent-bridge proposals, backup index lines).

## Docstrings — Google style

Every public module, class, function, and method has a Google-style
docstring. Required sections where applicable: `Args`, `Returns`,
`Raises`, `Yields`, `Example`. Enforced by:

- `ruff` with the `pydocstyle` rules enabled (`D` rules, Google
  convention).
- `pydoclint --style=google` as a stricter second check (catches
  type-vs-docstring drift).

Example shape:

```python
def fs_patch(path: str, patch: str, proposal_id: str) -> PatchResult:
    """Apply a unified diff to a file under a protected root.

    Args:
        path: Absolute path inside the mounted HA volumes.
        patch: Unified diff body.
        proposal_id: agent-bridge proposal id this patch belongs to.

    Returns:
        A PatchResult with `sha_before`, `sha_after`, and `bytes_changed`.

    Raises:
        ProtectedRootError: If `path` is outside the allowed roots.
        StorageRefusedError: If `path` resolves into `.storage/`
            without `unsafe_storage=True`.
        PatchApplyError: If the diff failed to apply cleanly.
    """
```

## Tests — branch coverage gate at 95 %

- **`pytest`** for everything. `pytest-asyncio` for async paths.
  `pytest-httpx` / `respx` for HA REST mocking. `hypothesis` for
  property tests on the patch + backup engines.
- **≥ 95 % branch coverage** on `node/src/` and
  `custom_components/openclaw_hass_node_assist/`. Measured by `coverage.py`
  with `--branch`. Coverage report is a CI gate; the live floor
  enforced today is 95% (we run well above it but the gate is set
  there so a routine refactor doesn't trip the build).
- Excluded from coverage requirement: `tests/`, generated
  protobuf/openapi clients, `__main__` bootstrap.
- Every new module ships with its tests in the same PR; "tests later"
  is not a valid review state.
- HA-touching integration tests use `pytest-homeassistant-custom-component`
  to spin up a real HA test harness for the shim.

## Lint and format

- **`ruff check`** with project rule set (`E`, `F`, `I`, `B`, `UP`,
  `SIM`, `D`, `RUF`, `ARG`, `PT`, `RET`, `TRY`).
- **`ruff format`** for formatting (replaces black).
- **`isort`** is bundled inside ruff.
- No exceptions left disabled in `pyproject.toml` without a comment.

## Other gates

- **`bandit`** for security smells (especially shell, deserialization,
  tempfile patterns — node runs `system.run`).
- **`pip-audit`** on the locked dependency set; CVE > medium blocks.
- **`pre-commit`** runs ruff, mypy, pydoclint locally; the same hooks
  re-run in CI.

## CI — GitHub Actions

Every PR runs the following jobs in parallel; merge requires all
green:

1. `lint` — `ruff check`, `ruff format --check`, `pydoclint`.
2. `typecheck` — `mypy --strict` + `pyright --strict`.
3. `test-node` — pytest with branch coverage on `node/`.
4. `test-shim` — pytest with branch coverage on
   `custom_components/openclaw_hass_node_assist/`, using
   `pytest-homeassistant-custom-component`.
5. `coverage-gate` — fails if total branch coverage on shipped code
   drops below 95 %.
6. `security` — `bandit`, `pip-audit`.
7. `docs-build` — markdown lint + dead-link check on `docs/`.
8. `addon-build` — HA add-on (app) build for `amd64`, `aarch64`, `armv7`
   via the official `home-assistant/builder` action (smoke build,
   not published).
9. `cross-review` — when the PR is opened by the Claude generator
   subagent, this job kicks off the Codex reviewer (see
   `docs/CONTRIBUTING.md`). Verdict comment is required before merge.

Branch protection on `main`: required checks = all of the above;
linear history; no force-push.

## Coverage waivers

There are none. If something is genuinely untestable (e.g. a
`raise SystemExit` in `__main__`), exclude it from coverage via a
`# pragma: no cover` *with* an inline reason and a `TODO` if it can
be refactored to be testable later. Reviewer must agree.
