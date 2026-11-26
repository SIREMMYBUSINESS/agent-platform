import { test } from "node:test";
import assert from "node:assert/strict";
import { handleIncoming, type IncomingEnv } from "../src/incoming";
import { twilioSignature } from "../src/twilio";

const ACCOUNT = "AC" + "a".repeat(32);
const CALL = "CA" + "c".repeat(32);
const TO = "+15550001111";
const BASE = {
  PUBLIC_ORIGIN: "https://voice.example.com",
  TWILIO_AUTH_TOKEN: "twilio-token",
  TWILIO_ACCOUNT_SID: ACCOUNT,
};

async function request(fields: Record<string, string>, sign = true) {
  const body = new URLSearchParams(fields);
  const signature = sign
    ? await twilioSignature(BASE.TWILIO_AUTH_TOKEN, `${BASE.PUBLIC_ORIGIN}/voice/incoming`, [...body.entries()])
    : "invalid";
  return new Request("https://worker.example/voice/incoming", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signature },
    body,
  });
}

function env(calls: Array<{ id: string; body: unknown }>, response = "<Response><Say>ok</Say></Response>"): IncomingEnv {
  return {
    ...BASE,
    VOICE_ADMISSION: {
      idFromName: (id: string) => id,
      get: (id: unknown) => ({
        fetch: async (_url: string, init?: RequestInit) => {
          calls.push({ id: String(id), body: JSON.parse(String(init?.body)) });
          return new Response(`<?xml version="1.0"?>${response}`, {
            status: 200, headers: { "Content-Type": "text/xml" },
          });
        },
      }),
    } as unknown as IncomingEnv["VOICE_ADMISSION"],
  };
}

test("valid signed webhook dispatches to the Durable Object keyed by AccountSid", async () => {
  const calls: Array<{ id: string; body: unknown }> = [];
  const res = await handleIncoming(await request({ AccountSid: ACCOUNT, CallSid: CALL, To: TO }), env(calls));
  assert.equal(res.status, 200);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].id, ACCOUNT);
  assert.deepEqual(calls[0].body, { accountSid: ACCOUNT, callSid: CALL, to: TO });
});

test("invalid signature is rejected before admission", async () => {
  const calls: Array<{ id: string; body: unknown }> = [];
  const res = await handleIncoming(await request({ AccountSid: ACCOUNT, CallSid: CALL, To: TO }, false), env(calls));
  assert.equal(res.status, 403);
  assert.equal(calls.length, 0);
});

test("invalid AccountSid, CallSid or To never reaches admission", async () => {
  for (const fields of [
    { AccountSid: "bad", CallSid: CALL, To: TO },
    { AccountSid: ACCOUNT, CallSid: "bad", To: TO },
    { AccountSid: ACCOUNT, CallSid: CALL, To: "12345" },
  ]) {
    const calls: Array<{ id: string; body: unknown }> = [];
    const res = await handleIncoming(await request(fields), env(calls));
    assert.equal(res.status, 400);
    assert.equal(calls.length, 0);
  }
});

test("configured account mismatch is rejected after signature validation", async () => {
  const calls: Array<{ id: string; body: unknown }> = [];
  const other = "AC" + "b".repeat(32);
  const body = new URLSearchParams({ AccountSid: other, CallSid: CALL, To: TO });
  const signature = await twilioSignature(BASE.TWILIO_AUTH_TOKEN, `${BASE.PUBLIC_ORIGIN}/voice/incoming`, [...body.entries()]);
  const req = new Request("https://worker.example/voice/incoming", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signature },
    body,
  });
  const res = await handleIncoming(req, env(calls));
  assert.equal(res.status, 403);
  assert.equal(calls.length, 0);
});
