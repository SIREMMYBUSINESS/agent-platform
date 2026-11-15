import { ReceptionistAgent } from "./agent";
import { issueStreamToken, verifyTwilioSignature } from "./security";

export { ReceptionistAgent };

export interface Env {
  RECEPTIONIST: DurableObjectNamespace;
  AI: Ai;
  TWILIO_AUTH_TOKEN: string;
  STREAM_SECRET: string;
  PLATFORM_API_KEY: string;
  PLATFORM_API: string;
  PUBLIC_URL: string;
  MAX_CALL_SECONDS?: string;
}

const twiml = (content: string, status = 200): Response =>
  new Response(content, {
    status,
    headers: {
      "content-type": "text/xml; charset=utf-8",
      "cache-control": "no-store",
    },
  });

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/health" && request.method === "GET") {
      return Response.json({ status: "ok" });
    }

    if (url.pathname === "/voice/incoming" && request.method === "POST") {
      const form = await request.formData();

      if (!await verifyTwilioSignature(request, form, env.TWILIO_AUTH_TOKEN, env.PUBLIC_URL)) {
        return new Response("Forbidden", { status: 403 });
      }

      const callSid = form.get("CallSid");
      const dialedNumber = form.get("To");

      if (
        typeof callSid !== "string" ||
        !/^CA[a-fA-F0-9]{32}$/.test(callSid) ||
        typeof dialedNumber !== "string" ||
        !/^\+[1-9]\d{6,14}$/.test(dialedNumber)
      ) {
        return new Response("Invalid call", { status: 400 });
      }

      let tenantId: string;
      let businessId: string;

      try {
        const lookupUrl = new URL("/voice/resolve-number", env.PLATFORM_API);
        lookupUrl.searchParams.set("to", dialedNumber);

        const lookup = await fetch(lookupUrl, {
          headers: {
            authorization: `Bearer ${env.PLATFORM_API_KEY}`,
            accept: "application/json",
          },
        });

        if (!lookup.ok) throw new Error("number_lookup_failed");

        const mapping = await lookup.json() as { tenant_id?: unknown; business_id?: unknown };
        if (
          typeof mapping.tenant_id !== "string" ||
          !mapping.tenant_id ||
          typeof mapping.business_id !== "string" ||
          !mapping.business_id
        ) {
          throw new Error("invalid_number_mapping");
        }

        tenantId = mapping.tenant_id;
        businessId = mapping.business_id;
      } catch {
        return twiml(
          `<?xml version="1.0" encoding="UTF-8"?>\n<Response><Say>We are unable to connect your call right now. Please try again later.</Say><Hangup/></Response>`,
          503,
        );
      }

      const streamToken = await issueStreamToken({
        callSid,
        tenantId,
        businessId,
        secret: env.STREAM_SECRET,
        nowSeconds: Math.floor(Date.now() / 1000),
      });

      const streamUrl = new URL(`/voice/stream/${encodeURIComponent(callSid)}`, env.PUBLIC_URL);
      streamUrl.protocol = "wss:";

      const parameters = [
        ["streamToken", streamToken],
        ["tenantId", tenantId],
        ["businessId", businessId],
      ]
        .map(([name, value]) => `<Parameter name="${escapeXml(name)}" value="${escapeXml(value)}"/>`)
        .join("");

      return twiml(
        `<?xml version="1.0" encoding="UTF-8"?>\n<Response><Connect><Stream url="${escapeXml(streamUrl.toString())}">${parameters}</Stream></Connect></Response>`,
      );
    }

    const match = url.pathname.match(/^\/voice\/stream\/(CA[a-fA-F0-9]{32})$/);
    if (match && request.headers.get("Upgrade")?.toLowerCase() === "websocket") {
      const callSid = match[1];
      const id = env.RECEPTIONIST.idFromName(callSid);
      return env.RECEPTIONIST.get(id).fetch(request);
    }

    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;

function escapeXml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
  })[char]!);
}
