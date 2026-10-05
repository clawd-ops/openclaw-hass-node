// Which gated calls an Assist session may make without a prompt.
//
// The node enforces the real rule (it resolves the verified caller and role from
// its own turn registry; see app/node/src/openclaw_node/approval_policy.py). The
// plugin cannot see the role, so it prompts unless the call is exempt for every
// Assist role. Tables are asserted equal to contracts/approval-gated-commands.json.

type ActionTable = Readonly<Record<string, readonly string[]>>;

/** Always needs approval, for every caller. */
export const DESTRUCTIVE: ActionTable = {
  "ha.config.automation": ["delete"],
  "ha.config.script": ["delete"],
  "ha.config.scene": ["delete"],
  "ha.config.helpers": ["delete"],
  "ha.config.area_registry": ["delete"],
  "ha.config.entity_registry": ["remove"],
  "ha.config.config_entries": ["disable"],
  "fs.delete": [""],
  "fs.move": [""],
  "fs.restore": [""],
  "ha.update_install": [""],
};

/** No approval for a verified HA admin or super_admin in a live Assist turn. */
export const USER_DIRECTED: ActionTable = {
  "ha.config.automation": ["save"],
  "ha.config.script": ["save"],
  "ha.config.scene": ["save"],
  "ha.config.helpers": ["create", "update"],
  "ha.config.area_registry": ["create", "update"],
  "ha.config.device_registry": ["update"],
  "ha.config.entity_registry": ["update"],
  "ha.config.config_entries": ["enable"],
  "ha.config.lovelace": ["save", "resources_create"],
};

/** Add-on lifecycle commands each Assist role may run with no approval. */
export const LIFECYCLE_NO_APPROVAL: Readonly<Record<string, ActionTable>> = {
  admin: { "ha.addon_start": [""], "ha.addon_restart": [""] },
  super_admin: { "ha.addon_start": [""], "ha.addon_stop": [""], "ha.addon_restart": [""] },
};

function listed(table: ActionTable, command: string, action: string): boolean {
  return Object.hasOwn(table, command) && table[command]?.includes(action) === true;
}

/**
 * True when a call from a live Assist session needs no prompt whichever Assist
 * role asks: user-directed or lifecycle-exempt for every role. Destructive pairs
 * are in neither table (asserted in tests). Add-on stop therefore still prompts (admin needs it).
 */
export function assistSkipsPrompt(command: string, action: string): boolean {
  return (
    listed(USER_DIRECTED, command, action) ||
    Object.values(LIFECYCLE_NO_APPROVAL).every((table) => listed(table, command, action))
  );
}
