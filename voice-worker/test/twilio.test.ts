import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import {
  makeStreamToken,
  parseStreamPath,
  safeEqual,
  streamPath,
  twilioSignature,
  validTwilioSignature,
  verifyStreamToken,
  type StreamScope,
} from "../src/twilio";

const TOKEN = "test-auth-token";
const URL_ = "https://voice.example.com/voice/incoming";
const SID = "CA" + "a".repeat(32);
const NOW = 1_800_000_000;
const scope = (over: Partial<StreamScope> = {}): StreamScope => ({
  callSid: SID,
  exp: NOW + 60,
  tenantId: "tenant-1",
  businessId: "biz_1",
  ...over,
});

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

test("stream token verifies for the exact call, tenant, business and expiry", async () => {
  const s = scope();
  const token = await makeStreamToken("s3cret", s);
  assert.equal(await verifyStreamToken("s3cret", { ...s, token }, NOW), true);
});

test("stream token rejects any tampering", async () => {
  const s = scope();
  const token = await makeStreamToken("s3cret", s);
  assert.equal(await verifyStreamToken("other-secret", { ...s, token }, NOW), false);
  assert.equal(await verifyStreamToken("s3cret", { ...s, tenantId: "tenant-2", token }, NOW), false);
  assert.equal(await verifyStreamToken("s3cret", { ...s, businessId: "biz_2", token }, NOW), false);
  assert.equal(await verifyStreamToken("s3cret", { ...s, callSid: "CA" + "b".repeat(32), token }, NOW), false);
  assert.equal(await verifyStreamToken("s3cret", { ...s, exp: s.exp + 1, token }, NOW), false);
});

test("stream token rejects expired and over-long windows", async () => {
  const s = scope();
  const token = await makeStreamToken("s3cret", s);
  assert.equal(await verifyStreamToken("s3cret", { ...s, token }, s.exp + 1), false);
  const far = scope({ exp: NOW + 10_000 });
  const farToken = await makeStreamToken("s3cret", far);
  assert.equal(await verifyStreamToken("s3cret", { ...far, token: farToken }, NOW), false);
});

test("stream path round-trips and rejects malformed paths", async () => {
  const s = scope();
  const token = await makeStreamToken("s3cret", s);
  const parsed = parseStreamPath(streamPath(s, token));
  assert.deepEqual(parsed, { ...s, token });
  assert.equal(parseStreamPath(`/voice/stream/${SID}/${s.exp}/tenant-1/${token}`), null);
  assert.equal(parseStreamPath(`/voice/stream/not-a-sid/${s.exp}/tenant-1/biz_1/${token}`), null);
  assert.equal(parseStreamPath(`/voice/stream/${SID}/${s.exp}/te nant/biz_1/${token}`), null);
  assert.equal(parseStreamPath(`/voice/stream/${SID}/${s.exp}/tenant-1/biz_1/zz`), null);
});

test("safeEqual", () => {
  assert.equal(safeEqual("abc", "abc"), true);
  assert.equal(safeEqual("abc", "abd"), false);
  assert.equal(safeEqual("abc", "ab"), false);
});
