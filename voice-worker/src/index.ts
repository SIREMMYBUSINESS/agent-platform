import { getAgentByName } from "agents";
import type { Env } from "./types";
import {
  CALL_SID_RE,
  escapeXml,
  makeStreamToken,
  validTwilioSignature,
  verifyStreamToken,
} from "./twilio";

export { ReceptionistAgent } from "./agent";

const PHONE_RE = /^\+?[0-9]{5,16}$/;
const STREAM_TOKEN_TTL_SECONDS = 120;
const MAX_WEBHOOK_BYTES = 10_000;

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);

    if (url.pathname === "/health") return new Response("ok");

    // 1. Twilio incoming-call webhook -> TwiML that opens the media stream.
    if (url.pathname === "/voice/incoming") {
      if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
      return handleIncoming(req, url, env);
    }

    // 2. Media stream WebSocket: /voice/stream/<callSid>/<exp>/<token>
    const m = url.pathname.match(/^\/voice\/stream\/([^/]+)\/(\d+)\/([0-9a-f]{64})$/);
    if (m) {
      if (req.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return new Response("Expected WebSocket", { status: 426 });
      }
      const [, callSid, exp, token] = m;
      // Reject before any Durable Object is created or woken.
      if (!(await verifyStreamToken(env.STREAM_SECRET, callSid, exp, token))) {
        return new Response("Forbidden", { status: 403 });
      }
      const agent = await getAgentByName(env.RECEPTIONIST, callSid);
      return agent.fetch(req);
    }

    return new Response("Not Found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;

async function handleIncoming(req: Request, url: URL, env: Env): Promise<Response> {
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
  if (!ok) return new Response("Forbidden", { status: 403 });

  const callSid = String(form.get("CallSid") ?? "");
  const from = String(form.get("From") ?? "");
  const to = String(form.get("To") ?? "");
  if (!CALL_SID_RE.test(callSid) || !PHONE_RE.test(from) || !PHONE_RE.test(to)) {
    return new Response("Bad Request", { status: 400 });
  }

  const exp = Math.floor(Date.now() / 1000) + STREAM_TOKEN_TTL_SECONDS;
  const token = await makeStreamToken(env.STREAM_SECRET, callSid, exp);
  const wsOrigin = env.PUBLIC_ORIGIN.replace(/^https:/, "wss:");

  // Twilio does not allow query strings on <Stream> URLs, so the token lives in the path.
  const twiml = `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="${escapeXml(`${wsOrigin}/voice/stream/${callSid}/${exp}/${token}`)}">
      <Parameter name="from" value="${escapeXml(from)}"/>
      <Parameter name="to" value="${escapeXml(to)}"/>
    </Stream>
  </Connect>
</Response>`;

  return new Response(twiml, { headers: { "Content-Type": "text/xml; charset=utf-8" } });
}
