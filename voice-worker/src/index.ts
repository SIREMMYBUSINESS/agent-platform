import { getAgentByName } from "agents";
import type { Env } from "./types";
import { handleIncoming } from "./incoming";
import { CALL_SID_RE, verifyStreamToken } from "./twilio";

export { ReceptionistAgent } from "./agent";

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname === "/health") return new Response("ok");

    if (url.pathname === "/voice/incoming") {
      if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
      return handleIncoming(req, env);
    }

    // Token validation happens before obtaining/waking the Durable Object.
    const match = url.pathname.match(/^\/voice\/stream\/([^/]+)\/(\d+)\/([0-9a-f]{64})$/);
    if (match) {
      if (req.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return new Response("Expected WebSocket", { status: 426 });
      }
      const [, callSid, expiry, token] = match;
      if (!CALL_SID_RE.test(callSid) || !await verifyStreamToken(env.STREAM_SECRET, callSid, expiry, token)) {
        return new Response("Forbidden", { status: 403 });
      }
      const agent = await getAgentByName(env.RECEPTIONIST, callSid);
      return agent.fetch(req);
    }

    return new Response("Not Found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
