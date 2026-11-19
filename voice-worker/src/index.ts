import { getAgentByName } from "agents";
import type { Env } from "./types";
import { handleIncoming } from "./incoming";
import { parseStreamPath, verifyStreamToken } from "./twilio";

export { ReceptionistAgent } from "./agent";

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

    return new Response("Not Found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
