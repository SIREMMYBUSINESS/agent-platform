import { test } from "node:test";
import assert from "node:assert/strict";
import { decodeMulaw8kToLinear16k, decodeMulawSample } from "../src/audio";
import {
  closeSocketSafely,
  FinalTranscriptDeduplicator,
  getCompletedTranscript,
  isCurrentSocketGeneration,
  isNova3SpeechStarted,
  openNova3WebSocket,
  parseNova3Message,
  type Nova3Env,
} from "../src/stt";

const ENV: Nova3Env = {
  CF_ACCOUNT_ID: "a".repeat(32),
  AI_GATEWAY_NAME: "voice-staging",
  AI_GATEWAY_AUTH_TOKEN: "test-gateway-token",
};

function socketFixture() {
  let accepted = false;
  let closed = false;
  const socket = {
    accept() { accepted = true; },
    close() { closed = true; },
    get accepted() { return accepted; },
    get closed() { return closed; },
  } as unknown as WebSocket & { readonly accepted: boolean; readonly closed: boolean };
  return socket;
}

test("G.711 μ-law decoder handles silence and positive/negative full-scale vectors", () => {
  assert.equal(decodeMulawSample(0xff), 0);
  assert.equal(decodeMulawSample(0x7f), 0);
  assert.equal(decodeMulawSample(0x80), 32124);
  assert.equal(decodeMulawSample(0x00), -32124);
});

test("μ-law 8 kHz audio becomes 16 kHz PCM16 little-endian with interpolation", () => {
  const pcm = decodeMulaw8kToLinear16k(new Uint8Array([0x80, 0xff]));
  const samples = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  assert.equal(pcm.byteLength, 8);
  assert.deepEqual(
    [samples.getInt16(0, true), samples.getInt16(2, true), samples.getInt16(4, true), samples.getInt16(6, true)],
    [32124, 16062, 0, 0],
  );
});

test("a 20 ms Twilio μ-law frame is resampled to 20 ms of 16 kHz PCM16", () => {
  const pcm = decodeMulaw8kToLinear16k(new Uint8Array(160).fill(0xff));
  assert.equal(pcm.byteLength, 640);
});

test("Nova-3 opens through the documented AI Gateway websocket upgrade contract", async () => {
  const socket = socketFixture();
  let requestedUrl: URL | undefined;
  let requestedHeaders: Headers | undefined;
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    requestedUrl = new URL(input instanceof Request ? input.url : String(input));
    requestedHeaders = new Headers(init?.headers);
    return Object.assign(new Response(null), { webSocket: socket });
  }) as typeof fetch;

  const opened = await openNova3WebSocket(ENV, fetcher);
  assert.equal(opened, socket);
  assert.equal(requestedUrl?.hostname, "gateway.ai.cloudflare.com");
  assert.equal(requestedUrl?.pathname, `/v1/${ENV.CF_ACCOUNT_ID}/${ENV.AI_GATEWAY_NAME}/workers-ai`);
  assert.equal(requestedUrl?.searchParams.get("model"), "@cf/deepgram/nova-3");
  assert.equal(requestedUrl?.searchParams.get("encoding"), "linear16");
  assert.equal(requestedUrl?.searchParams.get("sample_rate"), "16000");
  assert.equal(requestedUrl?.searchParams.get("interim_results"), "true");
  assert.equal(requestedUrl?.searchParams.get("vad_events"), "true");
  assert.equal(requestedHeaders?.get("upgrade"), "websocket");
  assert.equal(requestedHeaders?.get("cf-aig-authorization"), ENV.AI_GATEWAY_AUTH_TOKEN);
});

test("Nova-3 connection fails closed when gateway configuration is missing", async () => {
  let fetchCalled = false;
  const fetcher = (async () => { fetchCalled = true; return new Response(null); }) as typeof fetch;
  await assert.rejects(
    openNova3WebSocket({ ...ENV, AI_GATEWAY_AUTH_TOKEN: "" }, fetcher),
    /AI Gateway STT configuration is incomplete/,
  );
  assert.equal(fetchCalled, false);
});

test("Nova-3 connection rejects an HTTP response without an accepted websocket", async () => {
  const fetcher = (async () => new Response("upgrade required", { status: 426 })) as typeof fetch;
  await assert.rejects(openNova3WebSocket(ENV, fetcher), /handshake failed \(HTTP 426\)/);
});

test("Nova-3 parser accepts string and binary event frames and ignores malformed frames", async () => {
  const message = JSON.stringify({ type: "SpeechStarted" });
  assert.equal(isNova3SpeechStarted((await parseNova3Message(message))!), true);
  assert.equal(isNova3SpeechStarted((await parseNova3Message(new TextEncoder().encode(message).buffer))!), true);
  assert.equal(await parseNova3Message("not-json"), null);
  assert.equal(await parseNova3Message({ nope: true }), null);
});

test("interim and partial-final transcripts never become completed LLM utterances", () => {
  const interim = {
    is_final: false,
    speech_final: false,
    channel: { alternatives: [{ transcript: "hello there" }] },
  };
  const partialFinal = {
    is_final: true,
    speech_final: false,
    channel: { alternatives: [{ transcript: "hello there" }] },
  };
  assert.equal(getCompletedTranscript(interim), null);
  assert.equal(getCompletedTranscript(partialFinal), null);
});

test("one completed utterance queues one LLM turn; interim and duplicate finals queue none", () => {
  const gate = new FinalTranscriptDeduplicator();
  const events = [
    { is_final: false, speech_final: false, channel: { alternatives: [{ transcript: "hello there" }] } },
    { is_final: true, speech_final: false, channel: { alternatives: [{ transcript: "hello there" }] } },
    { is_final: true, speech_final: true, start: 1.25, duration: 0.8, channel: { alternatives: [{ transcript: "hello there" }] } },
    { is_final: true, speech_final: true, start: 1.25, duration: 0.8, channel: { alternatives: [{ transcript: "hello there" }] } },
  ];
  let llmTurns = 0;
  for (const event of events) {
    const completed = getCompletedTranscript(event);
    if (completed && gate.shouldAccept(completed, 1000 + llmTurns)) llmTurns += 1;
  }
  assert.equal(llmTurns, 1);
  assert.equal(gate.shouldAccept({ text: "hello there", start: 4.1, duration: 0.8 }, 1200), true);
  assert.equal(gate.shouldAccept({ text: "hello there", start: 1.25, duration: 0.8 }, 4000), true);
});

test("speech-start event is recognized for barge-in clearing", () => {
  assert.equal(isNova3SpeechStarted({ type: "SpeechStarted" }), true);
  assert.equal(isNova3SpeechStarted({ event: "SpeechStarted" }), true);
  assert.equal(isNova3SpeechStarted({ type: "Results" }), false);
});

test("stale socket generations are rejected and socket cleanup is safe", () => {
  assert.equal(isCurrentSocketGeneration(7, 7), true);
  assert.equal(isCurrentSocketGeneration(8, 7), false);
  const socket = socketFixture();
  closeSocketSafely(socket);
  assert.equal(socket.closed, true);
  closeSocketSafely(undefined);
});
