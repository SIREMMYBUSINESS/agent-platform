import { test } from "node:test";
import assert from "node:assert/strict";
import { redirectCallToFallback } from "../src/call-fallback";

const ACCOUNT_SID = `AC${"a".repeat(32)}`;
const CALL_SID = `CA${"b".repeat(32)}`;
const TOKEN = "unit-test-auth-token";

test("fallback updates the active call with safe provider-generated TwiML", async () => {
  let requestedUrl = "";
  let requestedInit: RequestInit | undefined;
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    requestedUrl = String(input);
    requestedInit = init;
    return new Response("{}", { status: 200 });
  }) as typeof fetch;

  await redirectCallToFallback({ accountSid: ACCOUNT_SID, callSid: CALL_SID, authToken: TOKEN }, fetcher);

  assert.equal(requestedUrl, `https://api.twilio.com/2010-04-01/Accounts/${ACCOUNT_SID}/Calls/${CALL_SID}.json`);
  assert.equal(requestedInit?.method, "POST");
  const headers = new Headers(requestedInit?.headers);
  assert.equal(headers.get("authorization"), `Basic ${btoa(`${ACCOUNT_SID}:${TOKEN}`)}`);
  const params = new URLSearchParams(String(requestedInit?.body));
  assert.match(params.get("Twiml") ?? "", /<Say>We are having trouble understanding the call/);
  assert.match(params.get("Twiml") ?? "", /<Hangup\/>/);
});

test("fallback rejects malformed call identifiers before making a request", async () => {
  let called = false;
  const fetcher = (async () => { called = true; return new Response(); }) as typeof fetch;
  await assert.rejects(
    redirectCallToFallback({ accountSid: "bad", callSid: CALL_SID, authToken: TOKEN }, fetcher),
    /configuration is invalid/,
  );
  assert.equal(called, false);
});

test("fallback surfaces a non-success Twilio response without leaking credentials", async () => {
  const fetcher = (async () => new Response("denied", { status: 403 })) as typeof fetch;
  await assert.rejects(
    redirectCallToFallback({ accountSid: ACCOUNT_SID, callSid: CALL_SID, authToken: TOKEN }, fetcher),
    /HTTP 403/,
  );
});
