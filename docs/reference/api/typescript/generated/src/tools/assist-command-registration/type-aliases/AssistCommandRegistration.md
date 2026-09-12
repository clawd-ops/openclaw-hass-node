[**@openclaw-hass-node/assist-tools**](../../../../README.md)

***

[@openclaw-hass-node/assist-tools](../../../../README.md) / [src/tools/assist-command-registration](../README.md) / AssistCommandRegistration

# Type Alias: AssistCommandRegistration

```ts
type AssistCommandRegistration = object;
```

Defined in: [src/tools/assist-command-registration.ts:25](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L25)

## Properties

### accepted\_tool\_params

```ts
accepted_tool_params: string[];
```

Defined in: [src/tools/assist-command-registration.ts:30](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L30)

***

### client\_side\_params

```ts
client_side_params: Record<string, AssistClientSideParam>;
```

Defined in: [src/tools/assist-command-registration.ts:35](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L35)

***

### descriptor

```ts
descriptor: string;
```

Defined in: [src/tools/assist-command-registration.ts:27](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L27)

***

### emitted\_params

```ts
emitted_params: Record<string, string | null>;
```

Defined in: [src/tools/assist-command-registration.ts:31](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L31)

***

### factory

```ts
factory: string;
```

Defined in: [src/tools/assist-command-registration.ts:28](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L28)

***

### injected\_node\_params

```ts
injected_node_params: Record<string, string>;
```

Defined in: [src/tools/assist-command-registration.ts:32](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L32)

***

### known\_unaccepted\_node\_params

```ts
known_unaccepted_node_params: Record<string, {
  issue: string;
  reason: string;
}>;
```

Defined in: [src/tools/assist-command-registration.ts:33](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L33)

***

### node\_command

```ts
node_command: string;
```

Defined in: [src/tools/assist-command-registration.ts:29](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L29)

***

### tool\_name

```ts
tool_name: string;
```

Defined in: [src/tools/assist-command-registration.ts:26](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L26)

***

### value\_transforms

```ts
value_transforms: Record<string, AssistValueTransform>;
```

Defined in: [src/tools/assist-command-registration.ts:34](https://github.com/clawd-ops/openclaw-hass-node/blob/main/plugins/openclaw-hass-node-assist-tools/src/tools/assist-command-registration.ts#L34)
