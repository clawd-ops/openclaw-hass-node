# Questions for Rob

> Append-only. Anything Clawd can't safely resolve overnight lands
> here with options + a recommendation. Read top-down; oldest open
> questions first. Answered items move to the bottom under
> "Resolved".

## Open

*(none currently)*

---

## Resolved

### Q1 — HACS shim default socket URL (hostname hash) — *resolved*

Verified live during UAT: the Supervisor hostname for the
`openclaw_hass_node` slug matches the existing default in
`custom_components/openclaw_gateway/const.py`. No re-release needed.
The config flow override remains the escape hatch if any deployment
sees a different hash. Tracked closed under TODO item #15.

### Q2 — `pairing_token` option: needed or remove? — *resolved*

**Decision: keep.** PR #93 (`feat(node): accept openclaw qr setup-code
envelope as pairing_token`) wired the `openclaw qr` bootstrap-token
flow through `pairing_token`, and dual-role pairing (PR #91 + #114)
depends on it. The option is documented in `docs/INSTALL.md` and
`docs/OVERVIEW.md` as the headless first-pair path; the operator
`openclaw nodes approve` path is still supported when no token is
configured. Tracked closed under TODO item #16.
