[**@openclaw-hass-node/assist-tools**](../../../../README.md)

***

[@openclaw-hass-node/assist-tools](../../../../README.md) / [src/tools/descriptors](../README.md) / HaCallServiceToolSchema

# Variable: HaCallServiceToolSchema

```ts
const HaCallServiceToolSchema: TObject<{
  data: TOptional<TRecord<"^.*$", TUnknown>>;
  domain: TString;
  node: TString;
  service: TString;
  service_data: TOptional<TRecord<"^.*$", TUnknown>>;
  target: TOptional<TObject<{
     area_id: TOptional<TUnion<[TString, TArray<TString>]>>;
     device_id: TOptional<TUnion<[TString, TArray<TString>]>>;
     entity_id: TOptional<TUnion<[TString, TArray<TString>]>>;
  }>>;
}>;
```
