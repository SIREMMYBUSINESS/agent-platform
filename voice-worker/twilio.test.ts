import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import {
  makeStreamToken,
  safeEqual,
  twilioSignature,
  validTwilioSignature,
  verifyStreamToken,
} from "../src/twilio";

const TOKEN = "test-auth-token";
const URL_ = "https://voice.example.com/voice/incoming";
const SID = "CA" + "a".repeat(32);

test("twilio signature matches reference HMAC-SHA1", async () => {
  const pairs: Array<[string, string]> = [
    ["To", "+15550001111"],
    ["CallSid", SID],
    ["From", "+15552223333"],
  ];
  const sorted = [...pairs].sort((a, b) => (a[0] < b[0] ? -1 : 1));
  const data = URL_ + sorted.map(([k, v]) => k + v).join("");
  const expected = createHmac("sha1", TOKEN).update(data).digest("base64");
  assert.equal(await twilioSignature(TOKEN, URL_, pairs), expected);
  assert.equal(await validTwilioSignature(TOKEN, URL_, pairs, expected), true);
});

test("invalid or missing signature is rejected", async () => {
  const pairs: Array<[string, string]> = [["CallSid", SID]];
  assert.equal(await validTwilioSignature(TOKEN, URL_, pairs, null), false);
  assert.equal(await validTwilioSignature(TOKEN, URL_, pairs, "bogus"), false);
  const good = await twilioSignature(TOKEN, URL_, pairs);
  assert.equal(await validTwilioSignature(TOKEN, "https://evil.example.com/voice/incoming", pairs, good), false);
});

test("stream token round-trips and rejects tampering/expiry", async () => {
  const exp = Math.floor(Date.now() / 1000) + 60;
  const tok = await makeStreamToken("s3cret", SID, exp);
  assert.equal(await verifyStreamToken("s3cret", SID, String(exp), tok), true);
  assert.equal(await verifyStreamToken("other", SID, String(exp), tok), false);
  assert.equal(await verifyStreamToken("s3cret", "CA" + "b".repeat(32), String(exp), tok), false);
  assert.equal(await verifyStreamToken("s3cret", SID, String(exp), tok, exp + 1), false);
  assert.equal(await verifyStreamToken("s3cret", "not-a-sid", String(exp), tok), false);
});

test("safeEqual", () => {
  assert.equal(safeEqual("abc", "abc"), true);
  assert.equal(safeEqual("abc", "abd"), false);
  assert.equal(safeEqual("abc", "ab"), false);
});
