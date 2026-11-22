import { test } from "node:test";
import { createHmac } from "node:crypto";
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

function twilioSdkFixtureSignature(url: string, pairs: Array<[string, string]>, token = ENV.TWILIO_AUTH_TOKEN): string {
  const grouped = new Map<string, string[]>();
  for (const [key, value] of pairs) {
    const values = grouped.get(key);
    if (values) values.push(value);
    else grouped.set(key, [value]);
  }
  let data = url;
  for (const key of [...grouped.keys()].sort()) {
    for (const value of [...new Set(grouped.get(key)!)].sort()) data += key + value;
  }
  return createHmac("sha1", token).update(data).digest("base64");
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

test("accepts a full Twilio call webhook fixture with AddOns and repeated fields", async () => {
  const fields: Array<[string, string]> = [
    ["AccountSid", "AC" + "1".repeat(32)],
    ["AddOns", JSON.stringify({ status: "completed", results: {} })],
    ["ApiVersion", "2010-04-01"],
    ["CallSid", SID],
    ["CallStatus", "ringing"],
    ["CallToken", "test-call-token"],
    ["Called", TO],
    ["Caller", "+15552223333"],
    ["Direction", "inbound"],
    ["From", "+15552223333"],
    ["To", TO],
    ["CallerCity", "Denver"],
    ["CallerCity", "Denver"],
    ["CallerState", "CO"],
  ];
  const body = new URLSearchParams(fields);
  const signature = twilioSdkFixtureSignature(`${ENV.PUBLIC_ORIGIN}/voice/incoming`, fields);
  const req = new Request("https://some-internal-host.workers.dev/voice/incoming", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signature },
    body,
  });
  const p = platform(ok);
  const res = await handleIncoming(req, ENV, p.fetchImpl);
  assert.equal(res.status, 200);
  assert.equal(p.calls.length, 1);
  assert.equal(p.calls[0].url.pathname, "/voice/resolve-number");
  assert.equal(p.calls[0].url.searchParams.get("to"), TO);
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


test("missing webhook configuration returns 500 before signature validation", async () => {
  const p = platform(ok);
  const missingOrigin = { ...ENV, PUBLIC_ORIGIN: "" } as IncomingEnv;
  const missingToken = { ...ENV, TWILIO_AUTH_TOKEN: "" } as IncomingEnv;
  assert.equal((await handleIncoming(await webhook({ CallSid: SID, To: TO }), missingOrigin, p.fetchImpl)).status, 500);
  assert.equal((await handleIncoming(await webhook({ CallSid: SID, To: TO }), missingToken, p.fetchImpl)).status, 500);
  assert.equal(p.calls.length, 0);
});

test("signature mismatch logs safe diagnostics only", async () => {
  const captured: string[] = [];
  const originalWarn = console.warn;
  console.warn = (...args: unknown[]) => { captured.push(args.map(String).join(" ")); };
  try {
    const p = platform(ok);
    const res = await handleIncoming(await webhook({ CallSid: SID, To: TO }, { sig: "invalid-signature" }), ENV, p.fetchImpl);
    assert.equal(res.status, 403);
  } finally {
    console.warn = originalWarn;
  }
  assert.equal(captured.length, 1);
  assert.match(captured[0], /twilio_signature_mismatch/);
  assert.match(captured[0], /voice\.example\.com\/voice\/incoming/);
  assert.match(captured[0], /CallSid/);
  assert.match(captured[0], /To/);
  assert.doesNotMatch(captured[0], /twilio-token/);
  assert.doesNotMatch(captured[0], /15550001111/);
  assert.doesNotMatch(captured[0], /invalid-signature/);
});


async function mismatchLog(formFields: Record<string, string>, configuredSid?: string) {
  const captured: string[] = [];
  const originalWarn = console.warn;
  console.warn = (...args: unknown[]) => { captured.push(args.map(String).join(" ")); };
  try {
    const env = { ...ENV, ...(configuredSid === undefined ? {} : { TWILIO_ACCOUNT_SID: configuredSid }) } as IncomingEnv;
    const res = await handleIncoming(await webhook(formFields, { sig: "invalid-signature" }), env, platform(ok).fetchImpl);
    assert.equal(res.status, 403);
  } finally {
    console.warn = originalWarn;
  }
  assert.equal(captured.length, 1);
  return { line: captured[0], json: JSON.parse(captured[0]) };
}

const SID_A = "AC" + "a".repeat(30) + "1111";
const SID_B = "AC" + "b".repeat(30) + "2222";

test("mismatch log says the token's account matches the request's account", async () => {
  const { json, line } = await mismatchLog({ CallSid: SID, To: TO, AccountSid: SID_A }, SID_A);
  assert.equal(json.account_sid_matches_config, true);
  assert.equal(json.request_account_sid_suffix, "1111");
  assert.equal(json.configured_account_sid_suffix, "1111");
  assert.ok(!line.includes(SID_A), "a full account SID must never be logged");
});

test("mismatch log flags a request from a different Twilio account than the configured one", async () => {
  const { json, line } = await mismatchLog({ CallSid: SID, To: TO, AccountSid: SID_A }, SID_B);
  assert.equal(json.account_sid_matches_config, false);
  assert.equal(json.request_account_sid_suffix, "1111");
  assert.equal(json.configured_account_sid_suffix, "2222");
  assert.ok(!line.includes(SID_A) && !line.includes(SID_B));
});

test("mismatch log reports null when the account cannot be compared", async () => {
  const noConfigured = await mismatchLog({ CallSid: SID, To: TO, AccountSid: SID_A });
  assert.equal(noConfigured.json.account_sid_matches_config, null);
  const noRequestSid = await mismatchLog({ CallSid: SID, To: TO }, SID_B);
  assert.equal(noRequestSid.json.account_sid_matches_config, null);
  assert.equal(noRequestSid.json.request_account_sid_suffix, null);
});
