import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { makeStreamToken, safeEqual, twilioSignature, validTwilioSignature, verifyStreamToken } from "../src/twilio";

const AUTH_TOKEN = "test-auth-token";
const URL = "https://voice.example.com/voice/incoming";
const CALL_SID = `CA${"a".repeat(32)}`;

test("Twilio signature matches HMAC-SHA1 reference", async () => {
  const pairs: Array<[string, string]> = [
    ["To", "+15550001111"],
    ["CallSid", CALL_SID],
    ["From", "+15552223333"],
  ];
  const data = URL + [...pairs].sort((a, b) => a[0].localeCompare(b[0])).map(([key, value]) => key + value).join("");
  const expected = createHmac("sha1", AUTH_TOKEN).update(data).digest("base64");
  assert.equal(await twilioSignature(AUTH_TOKEN, URL, pairs), expected);
  assert.equal(await validTwilioSignature(AUTH_TOKEN, URL, pairs, expected), true);
});

test("missing, invalid, and wrong-URL signatures are rejected", async () => {
  const pairs: Array<[string, string]> = [["CallSid", CALL_SID]];
  assert.equal(await validTwilioSignature(AUTH_TOKEN, URL, pairs, null), false);
  assert.equal(await validTwilioSignature(AUTH_TOKEN, URL, pairs, "bogus"), false);
  const valid = await twilioSignature(AUTH_TOKEN, URL, pairs);
  assert.equal(await validTwilioSignature(AUTH_TOKEN, "https://attacker.example/voice/incoming", pairs, valid), false);
});

test("stream tokens reject tampering, invalid CallSids, and expiry", async () => {
  const expiry = Math.floor(Date.now() / 1000) + 60;
  const token = await makeStreamToken("stream-secret", CALL_SID, expiry);
  assert.equal(await verifyStreamToken("stream-secret", CALL_SID, String(expiry), token), true);
  assert.equal(await verifyStreamToken("wrong-secret", CALL_SID, String(expiry), token), false);
  assert.equal(await verifyStreamToken("stream-secret", `CA${"b".repeat(32)}`, String(expiry), token), false);
  assert.equal(await verifyStreamToken("stream-secret", "not-a-call-sid", String(expiry), token), false);
  assert.equal(await verifyStreamToken("stream-secret", CALL_SID, String(expiry), token, expiry + 1), false);
});

test("safeEqual handles equal and unequal values", () => {
  assert.equal(safeEqual("abc", "abc"), true);
  assert.equal(safeEqual("abc", "abd"), false);
  assert.equal(safeEqual("abc", "ab"), false);
});
