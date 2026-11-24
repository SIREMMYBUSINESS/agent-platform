export interface Nova3Env {
  CF_ACCOUNT_ID: string;
  AI_GATEWAY_NAME: string;
  AI_GATEWAY_AUTH_TOKEN: string;
}

export interface Nova3Result {
  type?: string;
  event?: string;
  is_final?: boolean;
  speech_final?: boolean;
  start?: number;
  duration?: number;
  channel?: {
    alternatives?: Array<{ transcript?: string }>;
  };
  [key: string]: unknown;
}

export type Nova3SocketResponse = Response & { webSocket?: WebSocket };

/**
 * Open the documented Workers AI Deepgram Nova-3 realtime endpoint through AI Gateway.
 * Workers' fetch Upgrade contract exposes the accepted socket on Response.webSocket.
 */
export async function openNova3WebSocket(
  env: Nova3Env,
  fetcher: typeof fetch = fetch,
): Promise<WebSocket> {
  if (!env.CF_ACCOUNT_ID || !env.AI_GATEWAY_NAME || !env.AI_GATEWAY_AUTH_TOKEN) {
    throw new Error("AI Gateway STT configuration is incomplete");
  }

  const url = new URL(
    `https://gateway.ai.cloudflare.com/v1/${encodeURIComponent(env.CF_ACCOUNT_ID)}/${encodeURIComponent(env.AI_GATEWAY_NAME)}/workers-ai`,
  );
  url.searchParams.set("model", "@cf/deepgram/nova-3");
  url.searchParams.set("encoding", "linear16");
  url.searchParams.set("sample_rate", "16000");
  url.searchParams.set("interim_results", "true");
  url.searchParams.set("vad_events", "true");

  const response = await fetcher(url, {
    headers: {
      Upgrade: "websocket",
      "cf-aig-authorization": env.AI_GATEWAY_AUTH_TOKEN,
    },
  });
  const socket = (response as Nova3SocketResponse).webSocket;
  if (!socket) {
    throw new Error(`AI Gateway Nova-3 WebSocket handshake failed (HTTP ${response.status})`);
  }
  return socket;
}

export async function parseNova3Message(data: unknown): Promise<Nova3Result | null> {
  try {
    let payload: string;
    if (typeof data === "string") {
      payload = data;
    } else if (data instanceof ArrayBuffer) {
      payload = new TextDecoder().decode(data);
    } else if (ArrayBuffer.isView(data)) {
      payload = new TextDecoder().decode(data as Uint8Array);
    } else if (typeof Blob !== "undefined" && data instanceof Blob) {
      payload = await data.text();
    } else {
      return null;
    }
    const parsed: unknown = JSON.parse(payload);
    return parsed !== null && typeof parsed === "object" ? (parsed as Nova3Result) : null;
  } catch {
    return null;
  }
}

export function isNova3SpeechStarted(result: Nova3Result): boolean {
  return result.type === "SpeechStarted" || result.event === "SpeechStarted";
}

export interface CompletedTranscript {
  text: string;
  start?: number;
  duration?: number;
}

/** Return text only for the completed-utterance event; interim/final segments stay out of LLM. */
export function getCompletedTranscript(result: Nova3Result): CompletedTranscript | null {
  if (!result.is_final || !result.speech_final) return null;
  const transcript = result.channel?.alternatives?.[0]?.transcript;
  if (typeof transcript !== "string" || !transcript.trim()) return null;
  return {
    text: transcript.trim(),
    ...(typeof result.start === "number" ? { start: result.start } : {}),
    ...(typeof result.duration === "number" ? { duration: result.duration } : {}),
  };
}

/** Protect against retransmitted final events without suppressing later repeated phrases. */
export class FinalTranscriptDeduplicator {
  private previousKey = "";
  private previousAt = 0;

  shouldAccept(transcript: CompletedTranscript, now = Date.now()): boolean {
    const normalized = transcript.text.trim().replace(/\s+/g, " ").toLowerCase();
    const hasTiming = Number.isFinite(transcript.start) && Number.isFinite(transcript.duration);
    const key = hasTiming
      ? `${normalized}|${transcript.start!.toFixed(3)}|${transcript.duration!.toFixed(3)}`
      : normalized;
    if (key === this.previousKey && now - this.previousAt <= 2_000) return false;
    this.previousKey = key;
    this.previousAt = now;
    return true;
  }
}

export function isCurrentSocketGeneration(current: number, eventGeneration: number): boolean {
  return current === eventGeneration;
}

export function closeSocketSafely(socket?: WebSocket): void {
  try {
    socket?.close();
  } catch {
    // A socket that already completed its close handshake needs no further action.
  }
}
