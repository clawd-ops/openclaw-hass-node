// `hass-node.describe_image` gateway method.
//
// One vision call through the gateway's media-understanding runtime. There is
// no agent turn and no chat session, so the model has no tools by
// construction: it can only turn pixels into text.
//
// The add-on calls this over its operator WebSocket. Image bytes arrive as
// base64, are written to a private temp dir for the duration of the call, and
// are removed in `finally`. Neither the bytes nor the base64 are ever logged.

import { randomUUID } from "node:crypto";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

/** Gateway method name registered by this module. */
export const DESCRIBE_IMAGE_METHOD = "hass-node.describe_image";

/** Largest accepted decoded image. The runtime's own image cap is 10 MB. */
export const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
/** Longest accepted prompt, in characters. */
export const MAX_PROMPT_CHARS = 1000;
/** Default time budget for the vision call. */
export const DEFAULT_TIMEOUT_MS = 40_000;
const MIN_TIMEOUT_MS = 1_000;
const MAX_TIMEOUT_MS = 120_000;
// The runtime enforces `timeoutMs` itself; this is the outer backstop.
const TIMEOUT_GRACE_MS = 2_000;

/** Error codes returned to the caller. */
export type DescribeImageErrorCode =
  | "INVALID_PARAMS"
  | "UNSUPPORTED_MIME"
  | "IMAGE_TOO_LARGE"
  | "UNKNOWN_AGENT"
  | "VISION_UNAVAILABLE"
  | "TIMEOUT";

const EXTENSION_BY_MIME: Readonly<Record<string, string>> = {
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
};

const ALLOWED_KEYS: ReadonlySet<string> = new Set([
  "image_base64",
  "mime",
  "prompt",
  "agent_id",
  "timeout_ms",
  "model",
]);

const AGENT_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const MAX_MODEL_CHARS = 200;
const MODEL_REF = /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._:/@+-]*$/;
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;

/** Validated request parameters. */
export type DescribeImageRequest = {
  image: Buffer;
  mime: string;
  prompt: string;
  agentId?: string;
  /** Explicit `provider/model` override; wins over configured models. */
  model?: ModelRef;
  timeoutMs: number;
};

/** A failure that carries a caller-facing code. */
export class DescribeImageError extends Error {
  constructor(
    readonly code: DescribeImageErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "DescribeImageError";
  }
}

export type DescribeImageFailure = { code: DescribeImageErrorCode; message: string };

type Parsed =
  | { ok: true; value: DescribeImageRequest }
  | ({ ok: false } & DescribeImageFailure);

function fail(code: DescribeImageErrorCode, message: string): Parsed {
  return { ok: false, code, message };
}

function hasImageSignature(mime: string, bytes: Buffer): boolean {
  if (mime === "image/jpeg") return bytes.length > 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  if (mime === "image/png") {
    return bytes.length > 8 && bytes.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  }
  return (
    bytes.length > 12 &&
    bytes.subarray(0, 4).toString("latin1") === "RIFF" &&
    bytes.subarray(8, 12).toString("latin1") === "WEBP"
  );
}

/**
 * Validate raw gateway params. Strict: unknown keys are refused, the size cap
 * is checked on the encoded length before any decode, and the decoded bytes
 * must carry the signature of the declared mime.
 */
export function parseDescribeImageParams(raw: unknown): Parsed {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    return fail("INVALID_PARAMS", "params must be an object");
  }
  const params = raw as Record<string, unknown>;
  for (const key of Object.keys(params)) {
    if (!ALLOWED_KEYS.has(key)) return fail("INVALID_PARAMS", `unknown param: ${key}`);
  }

  const { mime, prompt, image_base64: encoded, agent_id: agentId, timeout_ms: timeoutRaw, model: modelRaw } = params;
  if (typeof mime !== "string" || !Object.hasOwn(EXTENSION_BY_MIME, mime)) {
    return fail("UNSUPPORTED_MIME", "mime must be image/jpeg, image/png or image/webp");
  }
  if (typeof prompt !== "string" || prompt.trim() === "" || prompt.length > MAX_PROMPT_CHARS) {
    return fail("INVALID_PARAMS", `prompt must be a non-empty string of at most ${MAX_PROMPT_CHARS} characters`);
  }
  if (typeof encoded !== "string" || encoded === "") {
    return fail("INVALID_PARAMS", "image_base64 must be a non-empty string");
  }
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  if (Math.floor((encoded.length * 3) / 4) - padding > MAX_IMAGE_BYTES) {
    return fail("IMAGE_TOO_LARGE", `image exceeds ${MAX_IMAGE_BYTES} bytes`);
  }
  if (encoded.length % 4 !== 0 || !BASE64.test(encoded)) {
    return fail("INVALID_PARAMS", "image_base64 is not valid base64");
  }
  const image = Buffer.from(encoded, "base64");
  if (image.length === 0 || image.length > MAX_IMAGE_BYTES) {
    return fail(image.length === 0 ? "INVALID_PARAMS" : "IMAGE_TOO_LARGE", "image size is out of range");
  }
  if (!hasImageSignature(mime, image)) {
    return fail("UNSUPPORTED_MIME", "image bytes do not match the declared mime");
  }

  let resolvedAgent: string | undefined;
  if (agentId !== undefined) {
    if (typeof agentId !== "string" || !AGENT_ID.test(agentId)) {
      return fail("INVALID_PARAMS", "agent_id is not a valid agent id");
    }
    resolvedAgent = agentId;
  }

  let model: ModelRef | undefined;
  if (modelRaw !== undefined) {
    const ref = typeof modelRaw === "string" ? parseModelRef(modelRaw.trim()) : undefined;
    if (typeof modelRaw !== "string" || modelRaw.trim().length > MAX_MODEL_CHARS || !MODEL_REF.test(modelRaw.trim()) || !ref) {
      return fail("INVALID_PARAMS", `model must be provider/model, at most ${MAX_MODEL_CHARS} characters`);
    }
    model = ref;
  }

  let timeoutMs = DEFAULT_TIMEOUT_MS;
  if (timeoutRaw !== undefined) {
    if (
      typeof timeoutRaw !== "number" ||
      !Number.isInteger(timeoutRaw) ||
      timeoutRaw < MIN_TIMEOUT_MS ||
      timeoutRaw > MAX_TIMEOUT_MS
    ) {
      return fail("INVALID_PARAMS", `timeout_ms must be an integer between ${MIN_TIMEOUT_MS} and ${MAX_TIMEOUT_MS}`);
    }
    timeoutMs = timeoutRaw;
  }

  return {
    ok: true,
    value: { image, mime, prompt, ...(resolvedAgent ? { agentId: resolvedAgent } : {}), ...(model ? { model } : {}), timeoutMs },
  };
}

type ModelRef = { provider: string; model: string };

type MediaDescribeParams = {
  filePath: string;
  mime: string;
  prompt: string;
  cfg: unknown;
  agentId?: string;
  timeoutMs: number;
};

type MediaUnderstandingRuntime = {
  describeImageFile(params: MediaDescribeParams): Promise<{ text?: string; provider?: string; model?: string }>;
  describeImageFileWithModel(
    params: MediaDescribeParams & { provider: string; model: string },
  ): Promise<{ text?: string; provider?: string; model?: string }>;
};

/** The slice of the plugin API this method touches. */
export type DescribeImageApi = {
  config: unknown;
  runtime: { mediaUnderstanding: MediaUnderstandingRuntime };
  logger?: { info?(msg: string): void; warn?(msg: string): void };
};

/** Gateway method handler options (the fields used here). */
export type GatewayMethodOptions = {
  params?: unknown;
  respond(ok: boolean, payload?: unknown, error?: { code: string; message: string }): void;
};

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function primaryOf(model: unknown): string | undefined {
  const value = typeof model === "string" ? model : record(model)?.primary;
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function parseModelRef(raw: string | undefined): ModelRef | undefined {
  if (!raw) return undefined;
  const slash = raw.indexOf("/");
  if (slash <= 0 || slash === raw.length - 1) return undefined;
  return { provider: raw.slice(0, slash), model: raw.slice(slash + 1) };
}

function configuredAgentIds(cfg: unknown): string[] {
  const agents = record(record(cfg)?.agents);
  const list = agents?.list;
  const ids: string[] = [];
  if (Array.isArray(list)) {
    for (const entry of list) {
      const id = record(entry)?.id;
      if (typeof id === "string" && id.trim()) ids.push(id.trim().toLowerCase());
    }
  }
  return ids;
}

function agentEntry(cfg: unknown, agentId: string): Record<string, unknown> | undefined {
  const list = record(record(cfg)?.agents)?.list;
  if (!Array.isArray(list)) return undefined;
  return list.map(record).find((entry) => typeof entry?.id === "string" && entry.id.trim().toLowerCase() === agentId);
}

/**
 * The explicit model to describe with: the image model if one is configured,
 * otherwise the agent's own primary model. Undefined means "let the runtime
 * auto-detect".
 *
 * An explicit model is used on purpose. `describeImageFile` skips image
 * understanding when the *active* model sees images natively (the pixels are
 * meant to travel with the chat turn), which returns no text here.
 */
export function resolveVisionModel(cfg: unknown, agentId: string | undefined): ModelRef | undefined {
  const defaults = record(record(cfg)?.agents)?.defaults;
  const imageModel = parseModelRef(primaryOf(record(defaults)?.imageModel));
  if (imageModel) return imageModel;
  const agentModel = agentId ? primaryOf(agentEntry(cfg, agentId)?.model) : undefined;
  return parseModelRef(agentModel ?? primaryOf(record(defaults)?.model));
}

function timeoutLike(error: unknown): boolean {
  return error instanceof Error && (error.name === "TimeoutError" || /time(d)? ?out/i.test(error.message));
}

function authFailureLike(error: unknown): boolean {
  return error instanceof Error && /api key|unauthori[sz]ed|\b40[13]\b|auth(entication|orization)?\b.*(fail|missing|required)|no .*credentials?/i.test(error.message);
}

class DeadlineError extends Error {}

async function withDeadline<T>(work: Promise<T>, ms: number): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new DeadlineError("deadline")), ms);
  });
  try {
    return await Promise.race([work, deadline]);
  } finally {
    clearTimeout(timer);
  }
}

/** Run the vision call and return the description. Throws `DescribeImageFailure`-shaped errors. */
export async function describeImage(
  api: DescribeImageApi,
  request: DescribeImageRequest,
): Promise<{ text: string; provider: string; model: string }> {
  const cfg = api.config;
  const agentId = request.agentId?.toLowerCase();
  if (agentId !== undefined) {
    const known = configuredAgentIds(cfg);
    // With no explicit roster the gateway has a single implicit agent.
    if (known.length > 0 ? !known.includes(agentId) : agentId !== "main") {
      throw new DescribeImageError("UNKNOWN_AGENT", "agent_id is not a configured agent");
    }
  }

  const dir = await mkdtemp(join(tmpdir(), "hass-describe-"));
  try {
    await chmod(dir, 0o700);
    const filePath = join(dir, `image.${EXTENSION_BY_MIME[request.mime]}`);
    await writeFile(filePath, request.image, { mode: 0o600, flag: "wx" });

    const base: MediaDescribeParams = {
      filePath,
      mime: request.mime,
      prompt: request.prompt,
      cfg,
      timeoutMs: request.timeoutMs,
      ...(agentId ? { agentId } : {}),
    };
    const explicit = request.model ?? resolveVisionModel(cfg, agentId);
    const media = api.runtime.mediaUnderstanding;
    const attempt = explicit
      ? media.describeImageFileWithModel({ ...base, ...explicit })
      : media.describeImageFile(base);

    const result = await withDeadline(attempt, request.timeoutMs + TIMEOUT_GRACE_MS);
    const text = result?.text?.trim();
    if (!text) {
      throw new DescribeImageError("VISION_UNAVAILABLE", "the vision model returned no description");
    }
    return {
      text,
      provider: result.provider ?? explicit?.provider ?? "",
      model: result.model ?? explicit?.model ?? "",
    };
  } catch (error) {
    if (error instanceof DescribeImageError) throw error;
    if (error instanceof DeadlineError || timeoutLike(error)) {
      throw new DescribeImageError("TIMEOUT", "the vision call timed out");
    }
    if (authFailureLike(error)) {
      throw new DescribeImageError(
        "VISION_UNAVAILABLE",
        "no API access for the vision model; set vision_model to a provider/model with direct API access",
      );
    }
    throw new DescribeImageError("VISION_UNAVAILABLE", "the vision call failed");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

/** Build the gateway method handler. */
export function createDescribeImageHandler(api: DescribeImageApi) {
  return async ({ params, respond }: GatewayMethodOptions): Promise<void> => {
    const requestId = randomUUID().slice(0, 8);
    const parsed = parseDescribeImageParams(params);
    if (!parsed.ok) {
      api.logger?.warn?.(`describe_image ${requestId}: refused (${parsed.code})`);
      respond(false, undefined, { code: parsed.code, message: parsed.message });
      return;
    }
    const { value } = parsed;
    try {
      const out = await describeImage(api, value);
      api.logger?.info?.(`describe_image ${requestId}: ok (${value.image.length} bytes)`);
      respond(true, out);
    } catch (error) {
      const failure =
        error instanceof DescribeImageError ? error : new DescribeImageError("VISION_UNAVAILABLE", "the vision call failed");
      api.logger?.warn?.(`describe_image ${requestId}: ${failure.code} (${value.image.length} bytes)`);
      respond(false, undefined, { code: failure.code, message: failure.message });
    }
  };
}

/**
 * Register the method.
 *
 * `profileAccess: "independent"`: the handler reads and writes no durable
 * user or session state; it only converts the supplied bytes to text, so there
 * is no profile for it to be authorised against. The default ("required") only
 * adds a verified-user-profile check for browser-style GitHub-authenticated
 * clients and would reject the add-on's device-paired operator connection if
 * that connection ever lacked a profile. Operator write scope is still
 * enforced separately.
 */
export function registerDescribeImage(api: DescribeImageApi & GatewayRegistrar): void {
  api.registerGatewayMethod(DESCRIBE_IMAGE_METHOD, createDescribeImageHandler(api), {
    scope: "operator.write",
    profileAccess: "independent",
  });
}

export type GatewayRegistrar = {
  registerGatewayMethod(
    name: string,
    handler: (opts: GatewayMethodOptions) => Promise<void> | void,
    opts?: { scope?: string; profileAccess?: "required" | "independent" },
  ): void;
};
