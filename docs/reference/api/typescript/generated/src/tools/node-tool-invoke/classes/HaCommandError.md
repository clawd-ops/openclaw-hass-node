[**@openclaw-hass-node/assist-tools**](../../../../README.md)

***

[@openclaw-hass-node/assist-tools](../../../../README.md) / [src/tools/node-tool-invoke](../README.md) / HaCommandError

# Class: HaCommandError

A failed operation, distinct from failure to reach the gateway/node.

## Extends

- `Error`

## Constructors

### Constructor

```ts
new HaCommandError(
   code,
   message,
   source,
   retryable?,
   details?,
   retryAfterMs?
): HaCommandError;
```

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

```ts
Error.constructor
```

## Properties

### code

```ts
readonly code: string;
```

***

### details?

```ts
readonly optional details?: unknown;
```

***

### retryable?

```ts
readonly optional retryable?: boolean;
```

***

### retryAfterMs?

```ts
readonly optional retryAfterMs?: number;
```

***

### source

```ts
readonly source: "node" | "ha" | "gateway" | "transport";
```
