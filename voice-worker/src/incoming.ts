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
  "PUBLIC_ORIGIN" | "PLATFORM_API_URL" | "PLATFORM_API_KEY" | "TWILIO_AUTH_TOKEN" | "STREAM_SECRET"
>;

const PHONE_RE = /^\+[1-9][0-9]{6,14}$/;
const MAX_WEBHOOK_BYTES = 10_000;
const LOOKUP_TIMEOUT_MS = 3000;

const xml = (body: string) =>
  new Response(`<?xml version="1.0" encoding="UTF-8"?>\n${body}`, {
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
  try {
    const url = new URL("/voice/resolve-number", env.PLATFORM_API_URL);
    url.searchParams.set("to", to);
    const res = await fetchImpl(url.toString(), {
      headers: { Authorization: `Bearer ${env.PLATFORM_API_KEY}`, Accept: "application/json" },
      signal: AbortSignal.timeout(LOOKUP_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { tenant_id?: unknown; business_id?: unknown };
    if (typeof body.tenant_id !== "string" || typeof body.business_id !== "string") return null;
    if (!SCOPE_ID_RE.test(body.tenant_id) || !SCOPE_ID_RE.test(body.business_id)) return null;
    return { tenantId: body.tenant_id, businessId: body.business_id };
  } catch {
    return null;
  }
}

export async function handleIncoming(
  req: Request,
  env: IncomingEnv,
  fetchImpl: typeof fetch = fetch,
): Promise<Response> {
  const url = new URL(req.url);

  const missing = (["PUBLIC_ORIGIN", "TWILIO_AUTH_TOKEN"] as const).filter((key) => !env[key]);
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
    // Safe diagnostics: no query values, form values, token, or signatures.
    console.warn(JSON.stringify({
      event: "twilio_signature_mismatch",
      signed_url: `${url.origin}${url.pathname}`,
      query_names: [...url.searchParams.keys()].sort(),
      param_names: pairs.map(([k]) => k).sort(),
      signature_header_present: Boolean(req.headers.get("X-Twilio-Signature")),
      token_present: Boolean(env.TWILIO_AUTH_TOKEN),
    }));
    return new Response("Forbidden", { status: 403 });
  }

  const callSid = String(form.get("CallSid") ?? "");
  const to = String(form.get("To") ?? "");
  if (!CALL_SID_RE.test(callSid) || !PHONE_RE.test(to)) {
    return new Response("Bad Request", { status: 400 });
  }

  // Tenant scope comes only from the platform, keyed by the (signed) dialed number.
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
