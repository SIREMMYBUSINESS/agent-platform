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

    if (url.pathname === "/health") return new Response("ok");

    // 1. Twilio incoming-call webhook -> signature check, tenant lookup, TwiML.
    if (url.pathname === "/voice/incoming") {
      if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
      return handleIncoming(req, env);
    }

    // 2. Media stream WebSocket: /voice/stream/<callSid>/<exp>/<tenantId>/<businessId>/<token>
    if (url.pathname.startsWith("/voice/stream/")) {
      if (req.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return new Response("Expected WebSocket", { status: 426 });
      }
      const parsed = parseStreamPath(url.pathname);
      // Reject before any Durable Object is created or woken.
      if (!parsed || !(await verifyStreamToken(env.STREAM_SECRET, parsed))) {
        return new Response("Forbidden", { status: 403 });
      }
      const agent = await getAgentByName(env.RECEPTIONIST, parsed.callSid);
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
