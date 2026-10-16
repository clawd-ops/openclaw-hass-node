import { existsSync, statSync } from "node:fs";
import { dirname } from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  createDescribeImageHandler,
  DEFAULT_TIMEOUT_MS,
  DESCRIBE_IMAGE_METHOD,
  MAX_IMAGE_BYTES,
  parseDescribeImageParams,
  registerDescribeImage,
  resolveVisionModel,
  type DescribeImageApi,
} from "./describe-image.js";

const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(64, 1)]);
const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(32)]);
const WEBP = Buffer.concat([Buffer.from("RIFF"), Buffer.alloc(4), Buffer.from("WEBP"), Buffer.alloc(16)]);

const goodParams = (over: Record<string, unknown> = {}) => ({
  image_base64: JPEG.toString("base64"),
  mime: "image/jpeg",
  prompt: "Describe the photo.",
  ...over,
});

const CONFIG = {
  agents: {
    defaults: { model: { primary: "openai/gpt-6-sol" } },
    list: [{ id: "clawd", model: "openai/gpt-6-astra" }, { id: "other" }],
  },
};

function makeApi(
  runtime: Partial<DescribeImageApi["runtime"]["mediaUnderstanding"]> = {},
  config: unknown = CONFIG,
) {
  const mediaUnderstanding = {
    describeImageFile: vi.fn(async () => ({ text: "auto", provider: "p", model: "m" })),
    describeImageFileWithModel: vi.fn(async () => ({ text: " A person at the door. ", model: "gpt-6-astra" })),
    ...runtime,
  };
  const logger = { info: vi.fn(), warn: vi.fn() };
  return { api: { config, runtime: { mediaUnderstanding }, logger } as DescribeImageApi, mediaUnderstanding, logger };
}

async function call(api: DescribeImageApi, params: unknown) {
  const respond = vi.fn();
  await createDescribeImageHandler(api)({ params, respond });
  expect(respond).toHaveBeenCalledTimes(1);
  return respond.mock.calls[0] as [boolean, unknown, { code: string; message: string } | undefined];
}

describe("parseDescribeImageParams", () => {
  it("accepts each supported mime with a matching signature", () => {
    for (const [mime, bytes] of [
      ["image/jpeg", JPEG],
      ["image/png", PNG],
      ["image/webp", WEBP],
    ] as const) {
      const parsed = parseDescribeImageParams(goodParams({ mime, image_base64: bytes.toString("base64") }));
      expect(parsed).toMatchObject({ ok: true, value: { mime, timeoutMs: DEFAULT_TIMEOUT_MS } });
    }
  });

  it.each([
    ["non-object", "x", "INVALID_PARAMS"],
    ["array", [], "INVALID_PARAMS"],
    ["unknown key", goodParams({ extra: 1 }), "INVALID_PARAMS"],
    ["gif mime", goodParams({ mime: "image/gif" }), "UNSUPPORTED_MIME"],
    ["missing mime", goodParams({ mime: undefined }), "UNSUPPORTED_MIME"],
    ["prototype mime", goodParams({ mime: "toString" }), "UNSUPPORTED_MIME"],
    ["signature mismatch", goodParams({ mime: "image/png" }), "UNSUPPORTED_MIME"],
    ["empty prompt", goodParams({ prompt: "  " }), "INVALID_PARAMS"],
    ["non-string prompt", goodParams({ prompt: 5 }), "INVALID_PARAMS"],
    ["prompt too long", goodParams({ prompt: "x".repeat(1001) }), "INVALID_PARAMS"],
    ["empty image", goodParams({ image_base64: "" }), "INVALID_PARAMS"],
    ["bad base64", goodParams({ image_base64: "not base64!!" }), "INVALID_PARAMS"],
    ["bad base64 length", goodParams({ image_base64: "abc" }), "INVALID_PARAMS"],
    ["bad agent id", goodParams({ agent_id: "../x" }), "INVALID_PARAMS"],
    ["non-string agent id", goodParams({ agent_id: 3 }), "INVALID_PARAMS"],
    ["timeout too small", goodParams({ timeout_ms: 10 }), "INVALID_PARAMS"],
    ["timeout too large", goodParams({ timeout_ms: 999_999 }), "INVALID_PARAMS"],
    ["fractional timeout", goodParams({ timeout_ms: 1500.5 }), "INVALID_PARAMS"],
  ])("refuses %s", (_name, params, code) => {
    expect(parseDescribeImageParams(params)).toMatchObject({ ok: false, code });
  });

  it("refuses an oversized image before decoding it", () => {
    const big = Buffer.alloc(MAX_IMAGE_BYTES + 1).toString("base64");
    expect(parseDescribeImageParams(goodParams({ image_base64: big }))).toMatchObject({
      ok: false,
      code: "IMAGE_TOO_LARGE",
    });
  });

  it("accepts an image exactly at the cap", () => {
    const edge = Buffer.concat([JPEG, Buffer.alloc(MAX_IMAGE_BYTES - JPEG.length)]);
    expect(parseDescribeImageParams(goodParams({ image_base64: edge.toString("base64") })).ok).toBe(true);
  });

  it("accepts a prompt of exactly 1000 chars and a valid timeout", () => {
    expect(parseDescribeImageParams(goodParams({ prompt: "x".repeat(1000), timeout_ms: 45_000 }))).toMatchObject({
      ok: true,
      value: { timeoutMs: 45_000 },
    });
  });
});

describe("model override", () => {
  it("validates provider/model and length", () => {
    expect(parseDescribeImageParams(goodParams({ model: "openai/gpt-6-astra" }))).toMatchObject({
      ok: true,
      value: { model: { provider: "openai", model: "gpt-6-astra" } },
    });
    expect(parseDescribeImageParams(goodParams({ model: "openrouter/openai/gpt-6" }))).toMatchObject({
      ok: true,
      value: { model: { provider: "openrouter", model: "openai/gpt-6" } },
    });
    for (const bad of ["gpt", "/x", "a/", 5, "a/b c", `a/${"x".repeat(200)}`]) {
      expect(parseDescribeImageParams(goodParams({ model: bad }))).toMatchObject({ ok: false, code: "INVALID_PARAMS" });
    }
  });

  it("wins over the configured agent model", async () => {
    const { api, mediaUnderstanding } = makeApi();
    const [ok] = await call(api, goodParams({ agent_id: "clawd", model: "openai/gpt-6-astra-2" }));
    expect(ok).toBe(true);
    expect(mediaUnderstanding.describeImageFileWithModel).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "openai", model: "gpt-6-astra-2" }),
    );
  });

  it("passes any provider through to the runtime unchanged", async () => {
    const { api, mediaUnderstanding } = makeApi();
    const [ok] = await call(api, goodParams({ model: "openrouter/openai/gpt-6" }));
    expect(ok).toBe(true);
    expect(mediaUnderstanding.describeImageFileWithModel).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "openrouter", model: "openai/gpt-6" }),
    );
  });

  it("maps an auth failure to VISION_UNAVAILABLE naming vision_model", async () => {
    const { api } = makeApi({
      describeImageFileWithModel: vi.fn(async () => {
        throw new Error('No API key found for provider "anthropic"');
      }),
    });
    const [ok, , error] = await call(api, goodParams());
    expect(ok).toBe(false);
    expect(error?.code).toBe("VISION_UNAVAILABLE");
    expect(error?.message).toBe("set vision_model to a model your gateway can call directly");
  });
});

describe("resolveVisionModel", () => {
  it("prefers the image model, then the agent primary, then the default primary", () => {
    expect(
      resolveVisionModel({ agents: { defaults: { imageModel: { primary: "google/vision-1" } }, list: [] } }, "clawd"),
    ).toEqual({ provider: "google", model: "vision-1" });
    expect(resolveVisionModel(CONFIG, "clawd")).toEqual({ provider: "openai", model: "gpt-6-astra" });
    expect(resolveVisionModel(CONFIG, "other")).toEqual({ provider: "openai", model: "gpt-6-sol" });
    expect(resolveVisionModel(CONFIG, undefined)).toEqual({ provider: "openai", model: "gpt-6-sol" });
  });

  it("finds an agents.entries agent's primary model when no image model is set", () => {
    const cfg = {
      agents: {
        defaults: { model: { primary: "openai/gpt-6-sol" } },
        entries: { Clawd: { model: { primary: "openai/gpt-6-astra" } }, plain: {} },
      },
    };
    expect(resolveVisionModel(cfg, "clawd")).toEqual({ provider: "openai", model: "gpt-6-astra" });
    expect(resolveVisionModel(cfg, "plain")).toEqual({ provider: "openai", model: "gpt-6-sol" });
    const withImage = { agents: { ...cfg.agents, defaults: { imageModel: "google/vision-1" } } };
    expect(resolveVisionModel(withImage, "clawd")).toEqual({ provider: "google", model: "vision-1" });
  });

  it("returns undefined for refs that are not provider/model", () => {
    expect(resolveVisionModel({ agents: { defaults: { model: "alias" } } }, undefined)).toBeUndefined();
    expect(resolveVisionModel({}, undefined)).toBeUndefined();
  });
});

describe("handler", () => {
  it("calls the runtime with the agent's model and returns text, provider and model", async () => {
    const { api, mediaUnderstanding, logger } = makeApi();
    const [ok, payload] = await call(api, goodParams({ agent_id: "clawd", timeout_ms: 45_000 }));
    expect(ok).toBe(true);
    expect(payload).toEqual({ text: "A person at the door.", provider: "openai", model: "gpt-6-astra" });
    expect(mediaUnderstanding.describeImageFile).not.toHaveBeenCalled();
    expect(mediaUnderstanding.describeImageFileWithModel).toHaveBeenCalledWith({
      filePath: expect.stringMatching(/hass-describe-.*\/image\.jpg$/),
      mime: "image/jpeg",
      prompt: "Describe the photo.",
      cfg: CONFIG,
      agentId: "clawd",
      timeoutMs: 45_000,
      provider: "openai",
      model: "gpt-6-astra",
    });
    expect(logger.info).toHaveBeenCalledTimes(1);
  });

  it("stages the file privately and removes it after success", async () => {
    let dir = "";
    let mode = 0;
    let content: Buffer | undefined;
    const { api } = makeApi({
      describeImageFileWithModel: vi.fn(async (p: { filePath: string }) => {
        dir = dirname(p.filePath);
        mode = statSync(dir).mode & 0o777;
        content = (await import("node:fs")).readFileSync(p.filePath);
        return { text: "ok" };
      }),
    });
    await call(api, goodParams());
    expect(mode).toBe(0o700);
    expect(content?.equals(JPEG)).toBe(true);
    expect(existsSync(dir)).toBe(false);
  });

  it("removes the temp dir when the runtime throws", async () => {
    let dir = "";
    const { api } = makeApi({
      describeImageFileWithModel: vi.fn(async (p: { filePath: string }) => {
        dir = dirname(p.filePath);
        throw new Error("boom with /secret/path");
      }),
    });
    const [ok, , error] = await call(api, goodParams());
    expect(ok).toBe(false);
    expect(error).toEqual({ code: "VISION_UNAVAILABLE", message: "the vision call failed" });
    expect(existsSync(dir)).toBe(false);
  });

  it("maps runtime timeouts to TIMEOUT and still cleans up", async () => {
    let dir = "";
    const { api } = makeApi({
      describeImageFileWithModel: vi.fn(async (p: { filePath: string }) => {
        dir = dirname(p.filePath);
        throw new Error("request timed out after 40s");
      }),
    });
    const [, , error] = await call(api, goodParams());
    expect(error?.code).toBe("TIMEOUT");
    expect(existsSync(dir)).toBe(false);
  });

  it("times out a hung runtime at the backstop deadline and cleans up", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      let dir = "";
      let started!: () => void;
      const hung = new Promise<void>((resolve) => (started = resolve));
      const { api } = makeApi({
        describeImageFileWithModel: vi.fn((p: { filePath: string }) => {
          dir = dirname(p.filePath);
          started();
          return new Promise<never>(() => {});
        }),
      });
      const respond = vi.fn();
      const pending = createDescribeImageHandler(api)({ params: goodParams({ timeout_ms: 1000 }), respond });
      await hung;
      await vi.advanceTimersByTimeAsync(3100);
      await pending;
      expect(respond).toHaveBeenCalledWith(false, undefined, expect.objectContaining({ code: "TIMEOUT" }));
      expect(existsSync(dir)).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports VISION_UNAVAILABLE when the model returns no text", async () => {
    const { api } = makeApi({ describeImageFileWithModel: vi.fn(async () => ({ text: "  " })) });
    const [ok, , error] = await call(api, goodParams());
    expect(ok).toBe(false);
    expect(error?.code).toBe("VISION_UNAVAILABLE");
  });

  it("falls back to the runtime's auto-detection when no model ref resolves", async () => {
    const { api, mediaUnderstanding } = makeApi({}, { agents: { list: [{ id: "clawd" }] } });
    const [ok, payload] = await call(api, goodParams({ agent_id: "clawd" }));
    expect(ok).toBe(true);
    expect(payload).toEqual({ text: "auto", provider: "p", model: "m" });
    expect(mediaUnderstanding.describeImageFileWithModel).not.toHaveBeenCalled();
    expect(mediaUnderstanding.describeImageFile).toHaveBeenCalledWith(
      expect.objectContaining({ agentId: "clawd", mime: "image/jpeg" }),
    );
  });

  it("refuses an agent id that is not configured, without touching the runtime", async () => {
    const { api, mediaUnderstanding } = makeApi();
    const [ok, , error] = await call(api, goodParams({ agent_id: "ghost" }));
    expect(ok).toBe(false);
    expect(error?.code).toBe("UNKNOWN_AGENT");
    expect(mediaUnderstanding.describeImageFileWithModel).not.toHaveBeenCalled();
  });

  it("matches agent ids case-insensitively", async () => {
    const { api, mediaUnderstanding } = makeApi();
    const [ok] = await call(api, goodParams({ agent_id: "Clawd" }));
    expect(ok).toBe(true);
    expect(mediaUnderstanding.describeImageFileWithModel).toHaveBeenCalledWith(
      expect.objectContaining({ agentId: "clawd" }),
    );
  });

  it("accepts only the implicit main agent when no roster is configured", async () => {
    const { api } = makeApi({}, {});
    expect((await call(api, goodParams({ agent_id: "main" })))[0]).toBe(true);
    expect((await call(api, goodParams({ agent_id: "clawd" })))[2]?.code).toBe("UNKNOWN_AGENT");
  });

  it("accepts agents from the agents.entries roster and refuses unknown ids", async () => {
    const { api, mediaUnderstanding } = makeApi({}, { agents: { entries: { clawd: { model: "openai/gpt-6-astra" }, Coding: {} } } });
    expect((await call(api, goodParams({ agent_id: "clawd" })))[0]).toBe(true);
    expect(mediaUnderstanding.describeImageFileWithModel).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "openai", model: "gpt-6-astra" }),
    );
    expect((await call(api, goodParams({ agent_id: "coding" })))[0]).toBe(true);
    expect((await call(api, goodParams({ agent_id: "ghost" })))[2]?.code).toBe("UNKNOWN_AGENT");
    expect((await call(api, goodParams({ agent_id: "main" })))[2]?.code).toBe("UNKNOWN_AGENT");
  });

  it("merges ids from agents.list and agents.entries", async () => {
    const { api } = makeApi({}, { agents: { list: [{ id: "other" }], entries: { clawd: {} } } });
    expect((await call(api, goodParams({ agent_id: "other" })))[0]).toBe(true);
    expect((await call(api, goodParams({ agent_id: "clawd" })))[0]).toBe(true);
    expect((await call(api, goodParams({ agent_id: "ghost" })))[2]?.code).toBe("UNKNOWN_AGENT");
  });

  it("falls back to main-only when entries is not an object or is empty", async () => {
    for (const entries of [[], "x", {}]) {
      const { api } = makeApi({}, { agents: { entries } });
      expect((await call(api, goodParams({ agent_id: "main" })))[0]).toBe(true);
      expect((await call(api, goodParams({ agent_id: "clawd" })))[2]?.code).toBe("UNKNOWN_AGENT");
    }
  });

  it("returns validation errors without calling the runtime and never logs image data", async () => {
    const { api, mediaUnderstanding, logger } = makeApi();
    const [ok, , error] = await call(api, goodParams({ mime: "image/gif" }));
    expect(ok).toBe(false);
    expect(error?.code).toBe("UNSUPPORTED_MIME");
    expect(mediaUnderstanding.describeImageFileWithModel).not.toHaveBeenCalled();
    await call(api, goodParams());
    const logged = JSON.stringify([...logger.info.mock.calls, ...logger.warn.mock.calls]);
    expect(logged).not.toContain(JPEG.toString("base64"));
    expect(logged).not.toContain("Describe the photo");
  });
});

describe("registerDescribeImage", () => {
  it("registers the method with operator.write scope and independent profile access", () => {
    const registerGatewayMethod = vi.fn();
    const { api } = makeApi();
    registerDescribeImage({ ...api, registerGatewayMethod });
    expect(registerGatewayMethod).toHaveBeenCalledWith(DESCRIBE_IMAGE_METHOD, expect.any(Function), {
      scope: "operator.write",
      profileAccess: "independent",
    });
  });
});
