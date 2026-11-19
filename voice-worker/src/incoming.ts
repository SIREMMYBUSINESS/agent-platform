import type { Env } from "./types";
import { CALL_SID_RE, escapeXml, makeStreamToken, validTwilioSignature } from "./twilio";

const MAX_WEBHOOK_BYTES = 10_000;
const PHONE_RE = /^\+[1-9][0-9]{6,14}$/;
const SCOPE_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const STREAM_TOKEN_TTL_SECONDS = 120;

type NumberMapping = { tenant_id?: unknown; business_id?: unknown };
type Fetcher = typeof fetch;

/** Validate Twilio, resolve the dialed number from the trusted platform directory, then emit TwiML. */
export async function handleIncoming(
  request: Request,
  env: Env,
  fetcher: Fetcher = fetch,
): Promise<Response> {
  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(contentLength) && contentLength > MAX_WEBHOOK_BYTES) {
    return new Response("Payload Too Large", { status: 413 });
  }
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/x-www-form-urlencoded")) {
    return new Response("Unsupported Media Type", { status: 415 });
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return new Response("Bad Request", { status: 400 });
  }

  const pairs: Array<[string, string]> = [];
  for (const [key, value] of form.entries()) {
    if (typeof value !== "string") return new Response("Bad Request", { status: 400 });
    pairs.push([key, value]);
  }
  const publicOrigin = env.PUBLIC_ORIGIN.replace(/\/$/, "");
  const requestUrl = new URL(request.url);
  const signedUrl = `${publicOrigin}${requestUrl.pathname}${requestUrl.search}`;
  if (!await validTwilioSignature(env.TWILIO_AUTH_TOKEN, signedUrl, pairs, request.headers.get("X-Twilio-Signature"))) {
    return new Response("Forbidden", { status: 403 });
  }

  const callSid = form.get("CallSid");
  const from = form.get("From");
  const to = form.get("To");
  if (
    typeof callSid !== "string" || !CALL_SID_RE.test(callSid) ||
    typeof from !== "string" || !PHONE_RE.test(from) ||
    typeof to !== "string" || !PHONE_RE.test(to)
  ) {
    return new Response("Bad Request", { status: 400 });
  }

  if (!env.PLATFORM_API_URL || !env.PLATFORM_API_KEY) {
    return unavailableTwiml();
  }

  let mapping: NumberMapping;
  try {
    const endpoint = new URL("/voice/resolve-number", env.PLATFORM_API_URL);
    endpoint.searchParams.set("to", to);
    const result = await fetcher(endpoint, {
      method: "GET",
      headers: {
        authorization: `Bearer ${env.PLATFORM_API_KEY}`,
        accept: "application/json",
      },
    });
    if (!result.ok) return unavailableTwiml();
    mapping = await result.json() as NumberMapping;
  } catch {
    return unavailableTwiml();
  }

  if (
    typeof mapping.tenant_id !== "string" || !SCOPE_ID_RE.test(mapping.tenant_id) ||
    typeof mapping.business_id !== "string" || !SCOPE_ID_RE.test(mapping.business_id)
  ) {
    return unavailableTwiml();
  }

  const expiresAt = Math.floor(Date.now() / 1000) + STREAM_TOKEN_TTL_SECONDS;
  const token = await makeStreamToken(env.STREAM_SECRET, callSid, expiresAt);
  const streamOrigin = new URL(env.PUBLIC_ORIGIN);
  if (streamOrigin.protocol !== "https:") return unavailableTwiml();
  streamOrigin.protocol = "wss:";
  const streamUrl = `${streamOrigin.origin}/voice/stream/${callSid}/${expiresAt}/${token}`;

  // Do not forward the caller's phone number. Tenant lookup used only the signed To number.
  const twiml = `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="${escapeXml(streamUrl)}">
      <Parameter name="to" value="${escapeXml(to)}"/>
    </Stream>
  </Connect>
</Response>`;
  return new Response(twiml, {
    headers: { "content-type": "text/xml; charset=utf-8", "cache-control": "no-store" },
  });
}

function unavailableTwiml(): Response {
  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?><Response><Say>We are unable to connect your call right now. Please try again later.</Say><Hangup/></Response>`,
    { status: 503, headers: { "content-type": "text/xml; charset=utf-8", "cache-control": "no-store" } },
  );
}
