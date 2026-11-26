// Authenticates Twilio's incoming-call webhook, then delegates admission to the
// account-scoped Durable Object. No per-isolate or source-IP rate limiting.
import { CALL_SID_RE, validTwilioSignature } from "./twilio";

import type { AdmissionNamespace } from "./types";

export type IncomingEnv = {
  PUBLIC_ORIGIN: string;
  TWILIO_AUTH_TOKEN: string;
  TWILIO_ACCOUNT_SID?: string;
  VOICE_ADMISSION: AdmissionNamespace;
};

const PHONE_RE = /^\+[1-9][0-9]{6,14}$/;
const ACCOUNT_SID_RE = /^AC[0-9a-f]{32}$/i;
const MAX_WEBHOOK_BYTES = 10_000;

const xml = (body: string, status = 200) =>
  new Response(`<?xml version="1.0" encoding="UTF-8"?>\n${body}`, {
    status,
    headers: { "Content-Type": "text/xml; charset=utf-8", "Cache-Control": "no-store" },
  });

const UNAVAILABLE = () =>
  xml("<Response><Say>We are unable to connect your call right now. Please try again later.</Say><Hangup/></Response>");

export async function handleIncoming(req: Request, env: IncomingEnv): Promise<Response> {
  const url = new URL(req.url);
  if (!env.PUBLIC_ORIGIN || !env.TWILIO_AUTH_TOKEN || !env.VOICE_ADMISSION) {
    console.error(JSON.stringify({ event: "voice_webhook_misconfigured" }));
    return UNAVAILABLE();
  }

  const len = Number(req.headers.get("content-length") ?? "0");
  if (len > MAX_WEBHOOK_BYTES) return new Response("Payload Too Large", { status: 413 });

  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return new Response("Bad Request", { status: 400 });
  }
  const pairs: Array<[string, string]> = [];
  for (const [key, value] of form.entries()) if (typeof value === "string") pairs.push([key, value]);

  const signedUrl = `${env.PUBLIC_ORIGIN}${url.pathname}${url.search}`;
  const signatureValid = await validTwilioSignature(
    env.TWILIO_AUTH_TOKEN, signedUrl, pairs, req.headers.get("X-Twilio-Signature"),
  );
  if (!signatureValid) {
    console.warn(JSON.stringify({
      event: "twilio_signature_mismatch",
      signature_header_present: Boolean(req.headers.get("X-Twilio-Signature")),
      token_present: Boolean(env.TWILIO_AUTH_TOKEN),
      param_names: pairs.map(([key]) => key).sort(),
    }));
    return new Response("Forbidden", { status: 403 });
  }

  // These identifiers are trusted only after the Twilio signature has passed.
  const accountSid = String(form.get("AccountSid") ?? "");
  const callSid = String(form.get("CallSid") ?? "");
  const to = String(form.get("To") ?? "");
  if (!ACCOUNT_SID_RE.test(accountSid) || !CALL_SID_RE.test(callSid) || !PHONE_RE.test(to)) {
    return new Response("Bad Request", { status: 400 });
  }
  if (env.TWILIO_ACCOUNT_SID && accountSid !== env.TWILIO_ACCOUNT_SID) {
    console.warn(JSON.stringify({ event: "twilio_account_sid_mismatch" }));
    return new Response("Forbidden", { status: 403 });
  }

  try {
    const id = env.VOICE_ADMISSION.idFromName(accountSid);
    const stub = env.VOICE_ADMISSION.get(id);
    const response = await stub.fetch("https://voice-admission.internal/admit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accountSid, callSid, to }),
    });
    // The admission service returns TwiML with HTTP 200 for every valid call,
    // including over-limit responses, so Twilio does not treat busy calls as webhook errors.
    if (response.status !== 200) {
      console.error(JSON.stringify({ event: "voice_admission_unexpected_status", status: response.status }));
      return UNAVAILABLE();
    }
    return response;
  } catch (error) {
    console.error(JSON.stringify({
      event: "voice_admission_unavailable",
      error_name: error instanceof Error ? error.name : "unknown",
    }));
    return UNAVAILABLE();
  }
}
