import { readFileSync, writeFileSync } from "node:fs";
import { expect, it } from "vitest";
import { CASE_COUNT, generateCases, renderFixture } from "./approval-bind-fixture-gen.js";

const PATH = new URL("../../../../contracts/approval-bind-generated.json", import.meta.url);

it("committed generated fixture is up to date", () => {
  if (process.env.UPDATE_APPROVAL_FIXTURE === "1") writeFileSync(PATH, renderFixture());
  expect(readFileSync(PATH, "utf8")).toBe(renderFixture());
  expect(generateCases()).toHaveLength(CASE_COUNT);
});
