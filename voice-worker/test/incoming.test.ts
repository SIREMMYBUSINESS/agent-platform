import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { handleIncoming } from "../src/incoming";
import type { Env } from "../src/types";

const ORIGIN = "https://voice.example.com";
const AUTH_TOKEN = "twilio-test-token";
const CALL_SID = `CA${"c".repeat(32)}`;
const FROM = "+15552223333";
const TO = "+15550001111";

function createEnv(): Env {
  return {
    PUBLIC_ORIGIN: ORIGIN,
    TWILIO_ACCOUNT_SID: "AC00000000000000000000000000000000",
    TWILIO_AUTH_TOKEN: AUTH_TOKEN,
    STREAM_SECRET: "a-long-test-stream-secret",
    PLATFORM_API_URL: "https://platform.example.test",
    PLATFORM_API_KEY: "test-platform-key",
    AI: { run: async () => ({}) } as unknown as Ai,
    RECEPTIONIST: {} as unknown as Env["RECEPTIONIST"],
  };
}

function signedRequest(): Request {
  const form = new URLSearchParams({ CallSid: CALL_SID, From: FROM, To: TO });
  const pairs = [...form.entries()].sort(([a], [b]) => a.localeCompare(b));
  let signed = `${ORIGIN}/voice/incoming`;
  for (const [key, value] of pairs) signed += key + value;
  const signature = createHmac("sha1", AUTH_TOKEN).update(signed).digest("base64");
  return new Request(`${ORIGIN}/voice/incoming`, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      "X-Twilio-Signature": signature,
    },
    body: form,
  });
}

test("incoming call resolves signed To number and returns a protected stream URL", async () => {
  let requested: URL | undefined;
  const response = await handleIncoming(signedRequest(), createEnv(), async (input, init) => {
    requested = new URL(String(input));
    assert.equal(init?.method, "GET");
    assert.equal(new Headers(init?.headers).get("authorization"), "Bearer test-platform-key");
    return Response.json({ tenant_id: "tenant_1", business_id: "business_1" });
  });

  assert.equal(response.status, 200);
  assert.equal(requested?.searchParams.get("to"), TO);
  const body = await response.text();
  assert.match(body, /<Connect>/);
  assert.match(body, /<Stream url="wss:\/\/voice\.example\.com\/voice\/stream\/CAc{32}\/\d+\/[0-9a-f]{64}">/);
  assert.ok(body.includes('<Parameter name="to" value="' + TO + '"/>'));
  assert.ok(!body.includes(FROM), "caller number must not be forwarded");
});

test("invalid Twilio signature never performs tenant lookup", async () => {
  const request = signedRequest();
  const headers = new Headers(request.headers);
  headers.set("X-Twilio-Signature", "invalid");
  const tampered = new Request(request.url, { method: "POST", headers, body: await request.clone().text() });
  let lookupCalled = false;
  const response = await handleIncoming(tampered, createEnv(), async () => {
    lookupCalled = true;
    return Response.json({ tenant_id: "tenant_1", business_id: "business_1" });
  });
  assert.equal(response.status, 403);
  assert.equal(lookupCalled, false);
});

test("number lookup failure fails closed without leaking platform details", async () => {
  const response = await handleIncoming(signedRequest(), createEnv(), async () => new Response("private error", { status: 404 }));
  assert.equal(response.status, 503);
  assert.doesNotMatch(await response.text(), /private error/);
});
