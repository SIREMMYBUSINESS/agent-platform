import { test } from "node:test";
import assert from "node:assert/strict";
import { VoiceAdmission } from "../src/admission";
import { twilioSignature } from "../src/twilio";

const ACCOUNT = "AC" + "a".repeat(32);
const CALL = "CA" + "c".repeat(32);
const TO = "+15550001111";

class MemoryStorage {
  values = new Map<string, unknown>();
  async get<T>(key: string): Promise<T | undefined> { return this.values.get(key) as T | undefined; }
  async put(key: string, value: unknown): Promise<void> { this.values.set(key, value); }
  async delete(key: string): Promise<boolean> { return this.values.delete(key); }
}
function setup(options: { businessLimit?: number; lookupStatus?: number } = {}) {
  const storage = new MemoryStorage();
  let lookups = 0;
  let lock: Promise<unknown> = Promise.resolve();
  const state = {
    storage,
    blockConcurrencyWhile: async <T>(fn: () => Promise<T>) => {
      const previous = lock;
      let release!: () => void;
      lock = new Promise<void>((resolve) => { release = resolve; });
      await previous;
      try { return await fn(); } finally { release(); }
    },
  } as unknown as DurableObjectState;
  const env = {
    PUBLIC_ORIGIN: "https://voice.example.com",
    PLATFORM_API_URL: "https://platform.example.com",
    PLATFORM_API_KEY: "platform-key",
    STREAM_SECRET: "stream-secret",
  } as never;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    lookups++;
    return options.lookupStatus && options.lookupStatus !== 200
      ? new Response("failed", { status: options.lookupStatus })
      : Response.json({
          tenant_id: "tenant-a", business_id: "business-a",
          voice_calls_per_minute: options.businessLimit ?? 10,
        });
  }) as typeof fetch;
  const admission = new VoiceAdmission(state, env);
  const request = (callSid: string) => new Request("https://admission.internal/admit", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accountSid: ACCOUNT, callSid, to: TO }),
  });
  return { admission, request, storage, get lookups() { return lookups; }, restore: () => { globalThis.fetch = originalFetch; } };
}

test("same CallSid reuses the stored TwiML without a second lookup or token", async () => {
  const s = setup();
  try {
    const first = await s.admission.fetch(s.request(CALL));
    const second = await s.admission.fetch(s.request(CALL));
    assert.equal(first.status, 200);
    assert.equal(second.status, 200);
    assert.equal(await first.text(), await second.text());
    assert.equal(s.lookups, 1);
    const times = await s.storage.get<number[]>("account");
    assert.equal(times?.length, 1);
  } finally { s.restore(); }
});

test("account-wide limit applies across different CallSids and returns HTTP 200 busy TwiML", async () => {
  const s = setup();
  try {
    for (let i = 0; i < 61; i++) {
      const sid = "CA" + i.toString(16).padStart(32, "0");
      const response = await s.admission.fetch(s.request(sid));
      assert.equal(response.status, 200);
      if (i === 60) assert.match(await response.text(), /We are busy/);
      else await response.text();
    }
    assert.equal(s.lookups, 1); // 30-second positive mapping cache
    const times = await s.storage.get<number[]>("account");
    assert.equal(times?.length, 60);
  } finally { s.restore(); }
});

test("per-business limit is enforced after lookup and cached mapping is positive-only", async () => {
  const s = setup({ businessLimit: 1 });
  try {
    const first = await s.admission.fetch(s.request(CALL));
    assert.match(await first.text(), /<Stream/);
    const secondSid = "CA" + "d".repeat(32);
    const second = await s.admission.fetch(s.request(secondSid));
    assert.equal(second.status, 200);
    assert.match(await second.text(), /We are busy/);
    assert.equal(s.lookups, 1);
  } finally { s.restore(); }
});

test("failed number resolution is not cached", async () => {
  const s = setup({ lookupStatus: 404 });
  try {
    const first = await s.admission.fetch(s.request(CALL));
    assert.match(await first.text(), /unable to connect/i);
    const secondSid = "CA" + "d".repeat(32);
    const second = await s.admission.fetch(s.request(secondSid));
    assert.match(await second.text(), /unable to connect/i);
    assert.equal(s.lookups, 2);
    assert.equal(await s.storage.get("number:" + TO), undefined);
  } finally { s.restore(); }
});
