[**@openclaw-hass-node/assist-tools**](../../../../README.md)

***

[@openclaw-hass-node/assist-tools](../../../../README.md) / [src/tools/assist-command-registration](../README.md) / AssistCommandRegistration

# Type Alias: AssistCommandRegistration

```ts
type AssistCommandRegistration = object;
```

## Properties

### accepted\_tool\_params

```ts
accepted_tool_params: string[];
```

***

### client\_side\_params

```ts
client_side_params: Record<string, AssistClientSideParam>;
```

***

### descriptor

```ts
descriptor: string;
```

***

### emitted\_params

```ts
emitted_params: Record<string, string | null>;
```

***

### factory

```ts
factory: string;
```

***

### injected\_node\_params

```ts
injected_node_params: Record<string, string>;
```

***

### known\_unaccepted\_node\_params

```ts
known_unaccepted_node_params: Record<string, {
  issue: string;
  reason: string;
}>;
```

***

### node\_command

```ts
node_command: string;
```

***

### tool\_name

```ts
tool_name: string;
```

***

### value\_transforms

```ts
value_transforms: Record<string, AssistValueTransform>;
```
