import { test } from "node:test";
import assert from "node:assert/strict";
import { handleIncoming, type IncomingEnv } from "../src/incoming";
import { parseStreamPath, twilioSignature, verifyStreamToken } from "../src/twilio";

const ENV: IncomingEnv = {
  PUBLIC_ORIGIN: "https://voice.example.com",
  PLATFORM_API_URL: "https://platform.example.com",
  PLATFORM_API_KEY: "svc-key",
  TWILIO_AUTH_TOKEN: "twilio-token",
  STREAM_SECRET: "stream-secret",
};
const SID = "CA" + "c".repeat(32);
const TO = "+15550001111";

async function webhook(fields: Record<string, string>, opts: { sign?: boolean; sig?: string } = {}) {
  const body = new URLSearchParams(fields);
  const sig =
    opts.sig ??
    (opts.sign === false
      ? ""
      : await twilioSignature(ENV.TWILIO_AUTH_TOKEN, `${ENV.PUBLIC_ORIGIN}/voice/incoming`, [...body.entries()]));
  const headers: Record<string, string> = { "Content-Type": "application/x-www-form-urlencoded" };
  if (sig) headers["X-Twilio-Signature"] = sig;
  return new Request("https://some-internal-host.workers.dev/voice/incoming", { method: "POST", headers, body });
}

function platform(respond: (url: URL, init?: RequestInit) => Response | Promise<Response>) {
  const calls: Array<{ url: URL; init?: RequestInit }> = [];
  const fetchImpl = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    calls.push({ url, init });
    return respond(url, init);
  }) as typeof fetch;
  return { calls, fetchImpl };
}

const ok = () => Response.json({ tenant_id: "tenant-1", business_id: "biz_1" });

test("valid call resolves tenant from the dialed number and issues a scoped stream URL", async () => {
  const p = platform(ok);
  const res = await handleIncoming(await webhook({ CallSid: SID, From: "+15552223333", To: TO }), ENV, p.fetchImpl);
  assert.equal(res.status, 200);
  const xml = await res.text();
  assert.match(xml, /<Connect><Stream url="wss:\/\/voice\.example\.com\/voice\/stream\//);
  assert.doesNotMatch(xml, /15552223333/); // caller number is not forwarded
  assert.doesNotMatch(xml, /<Parameter/);

  assert.equal(p.calls.length, 1);
  assert.equal(p.calls[0].url.pathname, "/voice/resolve-number");
  assert.equal(p.calls[0].url.searchParams.get("to"), TO);
  assert.equal((p.calls[0].init?.headers as Record<string, string>).Authorization, "Bearer svc-key");

  const path = new URL(xml.match(/url="([^"]+)"/)![1]).pathname;
  const parsed = parseStreamPath(path)!;
  assert.equal(parsed.callSid, SID);
  assert.equal(parsed.tenantId, "tenant-1");
  assert.equal(parsed.businessId, "biz_1");
  assert.equal(await verifyStreamToken(ENV.STREAM_SECRET, parsed), true);
});

test("invalid or missing Twilio signature returns 403 and never calls the platform", async () => {
  const p = platform(ok);
  const bad = await handleIncoming(await webhook({ CallSid: SID, To: TO }, { sig: "bogus" }), ENV, p.fetchImpl);
  const none = await handleIncoming(await webhook({ CallSid: SID, To: TO }, { sign: false }), ENV, p.fetchImpl);
  assert.equal(bad.status, 403);
  assert.equal(none.status, 403);
  assert.equal(p.calls.length, 0);
});

test("malformed CallSid or dialed number returns 400 without a lookup", async () => {
  const p = platform(ok);
  assert.equal((await handleIncoming(await webhook({ CallSid: "nope", To: TO }), ENV, p.fetchImpl)).status, 400);
  assert.equal((await handleIncoming(await webhook({ CallSid: SID, To: "12345" }), ENV, p.fetchImpl)).status, 400);
  assert.equal(p.calls.length, 0);
});

for (const [name, respond] of [
  ["unknown number (404)", () => new Response("no", { status: 404 })],
  ["platform error (500)", () => new Response("boom", { status: 500 })],
  ["unauthorized (401)", () => new Response("no", { status: 401 })],
  ["malformed body", () => Response.json({ nope: 1 })],
  ["non-string ids", () => Response.json({ tenant_id: 5, business_id: ["x"] })],
  ["ids with unsafe characters", () => Response.json({ tenant_id: "a/b", business_id: "c d" })],
  ["empty ids", () => Response.json({ tenant_id: "", business_id: "" })],
  ["network failure", () => { throw new Error("down"); }],
] as Array<[string, () => Response]>) {
  test(`fails closed on ${name}`, async () => {
    const p = platform(respond);
    const res = await handleIncoming(await webhook({ CallSid: SID, To: TO }), ENV, p.fetchImpl);
    const xml = await res.text();
    assert.match(xml, /<Hangup\/>/);
    assert.doesNotMatch(xml, /<Stream/);
  });
}
