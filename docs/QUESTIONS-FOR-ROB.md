# Questions for Rob

> Append-only. Anything Clawd can't safely resolve overnight lands
> here with options + a recommendation. Read top-down; oldest open
> questions first. Answered items move to the bottom under
> "Resolved".

## Open

### Q1 — HACS shim default socket URL (hostname hash)

The HACS companion shim (`custom_components/openclaw_gateway/const.py`) uses
`http://a0d7b954-openclaw-hass-node:8099` as the default socket URL.

The `a0d7b954` prefix is the expected HA-Supervisor hostname hash for the
`openclaw_hass_node` slug, but the exact hash is assigned by HA Supervisor
based on the slug and we haven't been able to verify it offline.

**Action needed:** during UAT Phase A3, check the actual hostname shown in the
add-on Network section of HA → Settings → Add-ons → OpenClaw Node. If it
differs from `a0d7b954-openclaw-hass-node`, update `const.py` with the correct
value and re-release.

**Low risk:** the config flow lets the user override the URL, so a wrong default
is not blocking — just cosmetically wrong.

---

### Q2 — `pairing_token` option: needed or remove?

The add-on `config.yaml` and `NodeConfig` include a `pairing_token` option.
The intent was a bootstrap token for headless first-time pairing without the
CLI approval step.

However, the standard OpenClaw node pairing flow is:
1. Node connects with no token.
2. Gateway emits `node.pair.requested`.
3. Operator runs `openclaw nodes approve <requestId>` once.
4. Node reconnects with the issued device token.

There is no standard "bootstrap token" for nodes (only for mobile QR flows).

**Options:**
- **Remove** `pairing_token` (cleaner, honest about what the protocol does).
- **Keep** it as a reserved field for a future silent/CIDR auto-approval path.

**Recommendation:** remove for P2 to keep the surface honest; the CIDR
auto-approve path already exists in the gateway config and doesn't need a
per-node token.

---

## Resolved

*(none yet)*
