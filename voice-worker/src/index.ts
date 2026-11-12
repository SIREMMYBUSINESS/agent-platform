import { getAgentByName } from "agents";
import { verifyTwilioSignature, signStreamToken } from "./security";

export { ReceptionistAgent } from "./agent";

export interface Env {
  RECEPTIONIST: DurableObjectNamespace;
  AI: Ai;
  TWILIO_AUTH_TOKEN: string;
  PLATFORM_API_KEY: string;
  PLATFORM_API: string;
  /** Public HTTPS origin configured in Twilio; avoids trusting proxy Host headers. */
  PUBLIC_URL: string;
  /** Existing, provisioned tenant/business context; never accepted from a call. */
  TENANT_ID: string;
  BUSINESS_ID: string;
  /** Optional maximum call duration, in seconds. */
  MAX_CALL_SECONDS?: string;
}

const xml = (body: string, status = 200) => new Response(body, {
  status,
  headers: { "content-type": "text/xml; charset=utf-8", "cache-control": "no-store" },
});

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname === "/voice/incoming" && req.method === "POST") {
      const form = await req.formData();
      if (!await verifyTwilioSignature(req, form, env.TWILIO_AUTH_TOKEN, env.PUBLIC_URL)) {
        return new Response("Forbidden", { status: 403 });
      }
      const callSid = form.get("CallSid");
      if (typeof callSid !== "string" || !/^CA[a-fA-F0-9]{32}$/.test(callSid)) {
        return xml("<Response><Say>We could not connect your call.</Say><Hangup/></Response>", 400);
      }
      const token = await signStreamToken(callSid, env.TWILIO_AUTH_TOKEN);
      const streamUrl = new URL(`/voice/stream/${encodeURIComponent(callSid)}`, env.PUBLIC_URL);
      streamUrl.protocol = "wss:";
      streamUrl.searchParams.set("token", token);
      const twiml = `<?xml version="1.0" encoding="UTF-8"?>\n<Response><Connect><Stream url="${escapeXml(streamUrl.toString())}"><Parameter name="streamToken" value="${escapeXml(token)}"/></Stream></Connect></Response>`;
      return xml(twiml);
    }

    const match = url.pathname.match(/^\/voice\/stream\/(CA[a-fA-F0-9]{32})$/);
    if (match && req.headers.get("Upgrade")?.toLowerCase() === "websocket") {
      const callSid = match[1];
      const token = url.searchParams.get("token") ?? "";
      if (!await verifyStreamToken(callSid, token, env.TWILIO_AUTH_TOKEN)) {
        return new Response("Forbidden", { status: 403 });
      }
      const agent = await getAgentByName(env.RECEPTIONIST, callSid);
      return agent.fetch(req);
    }

    if (url.pathname === "/health") return Response.json({ status: "ok" });
    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;

function escapeXml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;",
  })[char]!);
}

async function verifyStreamToken(callSid: string, token: string, secret: string): Promise<boolean> {
  const expected = await signStreamToken(callSid, secret);
  return constantTimeEqual(token, expected);
}

function constantTimeEqual(left: string, right: string): boolean {
  const a = new TextEncoder().encode(left);
  const b = new TextEncoder().encode(right);
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a[i] ?? 0) ^ (b[i] ?? 0);
  return diff === 0;
}
