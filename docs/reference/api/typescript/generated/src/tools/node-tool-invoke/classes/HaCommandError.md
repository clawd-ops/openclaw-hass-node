[**@openclaw-hass-node/assist-tools**](../../../../README.md)

***

[@openclaw-hass-node/assist-tools](../../../../README.md) / [src/tools/node-tool-invoke](../README.md) / HaCommandError

# Class: HaCommandError

Defined in: [src/tools/node-tool-invoke.ts:18](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/node-tool-invoke.ts#L18)

A failed operation, distinct from failure to reach the gateway/node.

## Extends

- `Error`

## Constructors

### Constructor

> **new HaCommandError**(`code`, `message`, `source`, `retryable?`, `details?`, `retryAfterMs?`): `HaCommandError`

Defined in: [src/tools/node-tool-invoke.ts:19](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/node-tool-invoke.ts#L19)

#### Parameters

##### code

`string`

##### message

`string`

##### source

`"node"` \| `"ha"` \| `"gateway"` \| `"transport"`

##### retryable?

`boolean`

##### details?

`unknown`

##### retryAfterMs?

`number`

#### Returns

`HaCommandError`

#### Overrides

`Error.constructor`

## Properties

### code

> `readonly` **code**: `string`

Defined in: [src/tools/node-tool-invoke.ts:20](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/node-tool-invoke.ts#L20)

***

### details?

> `readonly` `optional` **details?**: `unknown`

Defined in: [src/tools/node-tool-invoke.ts:24](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/node-tool-invoke.ts#L24)

***

### retryable?

> `readonly` `optional` **retryable?**: `boolean`

Defined in: [src/tools/node-tool-invoke.ts:23](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/node-tool-invoke.ts#L23)

***

### retryAfterMs?

> `readonly` `optional` **retryAfterMs?**: `number`

Defined in: [src/tools/node-tool-invoke.ts:25](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/node-tool-invoke.ts#L25)

***

### source

> `readonly` **source**: `"node"` \| `"ha"` \| `"gateway"` \| `"transport"`

Defined in: [src/tools/node-tool-invoke.ts:22](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/node-tool-invoke.ts#L22)
