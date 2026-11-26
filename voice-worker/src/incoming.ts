// Handler for Twilio's incoming-call webhook. Kept free of Workers-only imports so it is unit-testable.
// Fails closed: if the tenant cannot be resolved from the dialed number, the call is not connected.

import type { Env } from "./types";
import {
  CALL_SID_RE,
  SCOPE_ID_RE,
  STREAM_TOKEN_TTL_SECONDS,
  escapeXml,
  makeStreamToken,
  streamPath,
  validTwilioSignature,
} from "./twilio";

export type IncomingEnv = Pick<
  Env,
  "PUBLIC_ORIGIN" | "PLATFORM_API_URL" | "PLATFORM_API_KEY" | "TWILIO_AUTH_TOKEN" | "STREAM_SECRET" | "INCOMING_RATE_LIMITER" | "NUMBER_LOOKUP_RATE_LIMITER"
> & { TWILIO_ACCOUNT_SID?: string };

const PHONE_RE = /^\+[1-9][0-9]{6,14}$/;
const MAX_WEBHOOK_BYTES = 10_000;
const LOOKUP_TIMEOUT_MS = 3000;
const RATE_LIMITED = () =>
  xml(`<Response><Say>We are receiving too many calls right now. Please try again later.</Say><Hangup/></Response>`);

const xml = (body: string) =>
  new Response(`<?xml version="1.0" encoding="UTF-8"?>
${body}`, {
    headers: { "Content-Type": "text/xml; charset=utf-8", "Cache-Control": "no-store" },
  });

const UNAVAILABLE = () =>
  xml(`<Response><Say>We are unable to connect your call right now. Please try again later.</Say><Hangup/></Response>`);

type Scope = { tenantId: string; businessId: string };

/**
 * Platform contract: GET {PLATFORM_API_URL}/voice/resolve-number?to=<E.164>
 * Authorization: Bearer <PLATFORM_API_KEY>
 * 200 -> { "tenant_id": "...", "business_id": "..." }; anything else -> fail closed.
 */
async function resolveNumber(env: IncomingEnv, to: string, fetchImpl: typeof fetch): Promise<Scope | null> {
  // Logs only fixed codes: the reason, the HTTP status, and the exception class and code. Never the URL,
  // the dialed number, the key, or any message text (messages can echo the URL).
  const fail = (reason: string, status?: number, error?: { name: string; code?: string }) => {
    console.warn(JSON.stringify({
      event: "voice_number_lookup_failed",
      reason,
      ...(status === undefined ? {} : { http_status: status }),
      ...(error ? { error_name: error.name, ...(error.code ? { error_code: error.code } : {}) } : {}),
    }));
    return null;
  };

  let res: Response;
  try {
    const url = new URL("/voice/resolve-number", env.PLATFORM_API_URL);
    url.searchParams.set("to", to);
    res = await fetchImpl(url.toString(), {
      headers: { Authorization: `Bearer ${env.PLATFORM_API_KEY}`, Accept: "application/json" },
      signal: AbortSignal.timeout(LOOKUP_TIMEOUT_MS),
    });
  } catch (error) {
    const name = error instanceof Error ? error.name : "unknown";
    // Undici puts the socket error code (ECONNREFUSED, ENOTFOUND, ...) on error.cause.
    const cause = (error as { cause?: { code?: unknown } } | null)?.cause;
    const rawCode = (cause?.code ?? (error as { code?: unknown } | null)?.code) as unknown;
    const code = typeof rawCode === "string" && /^[A-Z_0-9]{2,40}$/.test(rawCode) ? rawCode : undefined;
    const reason = name === "TimeoutError" || name === "AbortError" ? "timeout" : "network_error";
    return fail(reason, undefined, { name, code });
  }

  if (!res.ok) return fail("http_error", res.status);

  let body: { tenant_id?: unknown; business_id?: unknown };
  try {
    body = (await res.json()) as { tenant_id?: unknown; business_id?: unknown };
  } catch {
    return fail("invalid_json");
  }

  if (typeof body.tenant_id !== "string" || typeof body.business_id !== "string") {
    return fail("missing_or_non_string_scope");
  }
  if (!SCOPE_ID_RE.test(body.tenant_id) || !SCOPE_ID_RE.test(body.business_id)) {
    return fail("invalid_scope_format");
  }

  console.info(JSON.stringify({ event: "voice_number_lookup_success" }));
  return { tenantId: body.tenant_id, businessId: body.business_id };
}
export async function handleIncoming(
  req: Request,
  env: IncomingEnv,
  fetchImpl: typeof fetch = fetch,
): Promise<Response> {
  const url = new URL(req.url);

  const missing = (["PUBLIC_ORIGIN", "TWILIO_AUTH_TOKEN", "STREAM_SECRET", "PLATFORM_API_KEY", "PLATFORM_API_URL"] as const).filter((key) => !env[key]);
  if (missing.length) {
    console.error(`voice webhook misconfigured; missing: ${missing.join(", ")}`);
    return new Response("Service misconfigured", { status: 500 });
  }

  const len = Number(req.headers.get("content-length") ?? "0");
  if (len > MAX_WEBHOOK_BYTES) return new Response("Payload Too Large", { status: 413 });

  const form = await req.formData();
  const pairs: Array<[string, string]> = [];
  for (const [k, v] of form.entries()) if (typeof v === "string") pairs.push([k, v]);

  // Validate against the configured public origin, never the Host/X-Forwarded-* headers.
  const signedUrl = `${env.PUBLIC_ORIGIN}${url.pathname}${url.search}`;
  const ok = await validTwilioSignature(
    env.TWILIO_AUTH_TOKEN,
    signedUrl,
    pairs,
    req.headers.get("X-Twilio-Signature"),
  );
  if (!ok) {
    const requestSid = String(form.get("AccountSid") ?? "");
    const configuredSid = env.TWILIO_ACCOUNT_SID ?? "";
    // Safe diagnostics: no query values, form values, token, or signatures.
    console.warn(JSON.stringify({
      event: "twilio_signature_mismatch",
      signed_url: `${env.PUBLIC_ORIGIN}${url.pathname}`,
      query_names: [...url.searchParams.keys()].sort(),
      param_names: pairs.map(([k]) => k).sort(),
      signature_header_present: Boolean(req.headers.get("X-Twilio-Signature")),
      token_present: Boolean(env.TWILIO_AUTH_TOKEN),
      // Compare account identifiers without logging the full SID.
      account_sid_matches_config: requestSid && configuredSid ? requestSid === configuredSid : null,
      request_account_sid_suffix: requestSid ? requestSid.slice(-4) : null,
      configured_account_sid_suffix: configuredSid ? configuredSid.slice(-4) : null,
    }));
    return new Response("Forbidden", { status: 403 });
  }

  const callSid = String(form.get("CallSid") ?? "");
  const to = String(form.get("To") ?? "");
  if (!CALL_SID_RE.test(callSid) || !PHONE_RE.test(to)) {
    return new Response("Bad Request", { status: 400 });
  }

  // Tenant scope comes only from the platform, keyed by the (signed) dialed number.
  try {
    const incomingLimit = await env.INCOMING_RATE_LIMITER.limit({ key: to });
    if (!incomingLimit.success) return RATE_LIMITED();
    const lookupLimit = await env.NUMBER_LOOKUP_RATE_LIMITER.limit({ key: to });
    if (!lookupLimit.success) return RATE_LIMITED();
  } catch (error) {
    const name = error instanceof Error ? error.name : "unknown";
    console.error(JSON.stringify({ event: "voice_rate_limit_unavailable", error_name: name }));
    return UNAVAILABLE();
  }

  try {
    const incomingLimit = await env.INCOMING_RATE_LIMITER.limit({ key: to });
    if (!incomingLimit.success) return RATE_LIMITED();
    const lookupLimit = await env.NUMBER_LOOKUP_RATE_LIMITER.limit({ key: to });
    if (!lookupLimit.success) return RATE_LIMITED();
  } catch (error) {
    const name = error instanceof Error ? error.name : "unknown";
    console.error(JSON.stringify({ event: "voice_rate_limit_unavailable", error_name: name }));
    return UNAVAILABLE();
  }

  const scope = await resolveNumber(env, to, fetchImpl);
  if (!scope) return UNAVAILABLE();

  const exp = Math.floor(Date.now() / 1000) + STREAM_TOKEN_TTL_SECONDS;
  const stream = { callSid, exp, ...scope };
  const token = await makeStreamToken(env.STREAM_SECRET, stream);
  const wsOrigin = env.PUBLIC_ORIGIN.replace(/^https:/, "wss:");

  // Twilio does not allow query strings on <Stream> URLs, so the signed scope lives in the path.
  return xml(
    `<Response><Connect><Stream url="${escapeXml(`${wsOrigin}${streamPath(stream, token)}`)}"/></Connect></Response>`,
  );
}
