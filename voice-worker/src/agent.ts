import type { Env } from "./index";
import { verifyStreamToken } from "./security";

type CallContext = {
  callSid: string;
  tenantId: string;
  businessId: string;
};

type TwilioEvent = {
  event?: string;
  start?: {
    streamSid?: string;
    callSid?: string;
    customParameters?: Record<string, string>;
  };
  media?: { payload?: string };
  streamSid?: string;
};

type AiResponse = {
  response?: string;
  webSocket?: WebSocket;
};

const SYSTEM_PROMPT = `You are a friendly, concise phone receptionist. Keep replies to at most two short sentences. Help callers with general information and collect the reason for the call. Never invent business facts or availability. Booking, message delivery, call transfer, and other external tools are disabled; do not claim you performed those actions. For an emergency, tell the caller to contact local emergency services immediately.`;

export class ReceptionistAgent {
  private readonly state: DurableObjectState;
  private readonly env: Env;
  private socket?: WebSocket;
  private stt?: WebSocket;
  private context?: CallContext;
  private streamSid?: string;
  private speaking = false;
  private closed = false;
  private turns = 0;
  private generation = 0;
  private history: Array<{ role: "user" | "assistant"; content: string }> = [];

  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;

    this.state.storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS transcript (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        text TEXT NOT NULL,
        ts INTEGER NOT NULL
      )
    `);
  }

  async fetch(request: Request): Promise<Response> {
    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
      return new Response("Expected WebSocket", { status: 426 });
    }

    const pair = new WebSocketPair();
    const client = pair[0];
    const server = pair[1];

    server.accept();
    this.socket = server;

    server.addEventListener("message", (event) => {
      void this.onMessage(event.data);
    });
    server.addEventListener("close", () => {
      void this.finish();
    });
    server.addEventListener("error", () => {
      void this.finish();
    });

    return new Response(null, { status: 101, webSocket: client });
  }

  async alarm(): Promise<void> {
    await this.finish();
  }

  private async onMessage(raw: string | ArrayBuffer): Promise<void> {
    let event: TwilioEvent;

    try {
      event = JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw)) as TwilioEvent;
    } catch {
      return;
    }

    if (event.event === "start" && event.start) {
      const parameters = event.start.customParameters ?? {};
      const callSid = event.start.callSid ?? "";
      const tenantId = parameters.tenantId ?? "";
      const businessId = parameters.businessId ?? "";
      const streamToken = parameters.streamToken ?? "";

      if (
        !/^CA[a-fA-F0-9]{32}$/.test(callSid) ||
        !tenantId ||
        !businessId ||
        !await verifyStreamToken({
          token: streamToken,
          callSid,
          tenantId,
          businessId,
          secret: this.env.STREAM_SECRET,
          nowSeconds: Math.floor(Date.now() / 1000),
        })
      ) {
        this.socket?.close(1008, "Invalid stream authorization");
        return;
      }

      this.context = { callSid, tenantId, businessId };
      this.streamSid = event.start.streamSid;

      const maxSeconds = Math.max(60, Math.min(7200, Number(this.env.MAX_CALL_SECONDS ?? 1800)));
      await this.state.storage.setAlarm(Date.now() + maxSeconds * 1000);

      try {
        await this.openStt();
      } catch {
        await this.say("I am sorry, our phone assistant is temporarily unavailable.");
        return;
      }

      await this.say("Thanks for calling. How can I help you today?");
      return;
    }

    if (event.event === "media" && event.media?.payload) {
      try {
        const audio = decodeBase64(event.media.payload);
        if (this.stt?.readyState === WebSocket.OPEN) {
          this.stt.send(audio);
        }
      } catch {
        // Ignore malformed media frames; do not log audio or payload data.
      }
      return;
    }

    if (event.event === "stop") {
      await this.finish();
    }
  }

  private async openStt(): Promise<void> {
    const result = await this.env.AI.run(
      "@cf/deepgram/nova-3",
      {
        encoding: "mulaw",
        sample_rate: 8000,
        interim_results: true,
        vad_events: true,
      },
      { websocket: true },
    ) as AiResponse;

    if (!result.webSocket) {
      throw new Error("stt_websocket_unavailable");
    }

    this.stt = result.webSocket;
    this.stt.accept();

    const generation = ++this.generation;
    this.stt.addEventListener("message", (event) => {
      if (generation !== this.generation) return;
      void this.onSttMessage(event.data);
    });
  }

  private async onSttMessage(raw: unknown): Promise<void> {
    let result: Record<string, any>;

    try {
      result = JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw as ArrayBuffer)) as Record<string, any>;
    } catch {
      return;
    }

    if ((result.type === "SpeechStarted" || result.event === "SpeechStarted") && this.speaking) {
      this.socket?.send(JSON.stringify({ event: "clear", streamSid: this.streamSid }));
      this.speaking = false;
    }

    const text = result.channel?.alternatives?.[0]?.transcript;
    if (result.is_final && result.speech_final && typeof text === "string" && text.trim()) {
      await this.reply(text.trim());
    }
  }

  private async reply(text: string): Promise<void> {
    if (this.closed || !this.context || this.turns >= 100) return;

    this.turns += 1;
    this.history.push({ role: "user", content: text });
    this.saveTranscript("user", text);

    const result = await this.env.AI.run(
      "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
      {
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          ...this.history.slice(-20),
        ],
        max_tokens: 160,
        temperature: 0.2,
      },
    ) as AiResponse;

    const response = result.response?.trim();
    if (!response) return;

    this.history.push({ role: "assistant", content: response });
    this.saveTranscript("assistant", response);
    await this.say(response);
  }

  private async say(text: string): Promise<void> {
    if (this.closed || !this.streamSid) return;
    this.speaking = true;

    try {
      const response = await this.env.AI.run(
        "@cf/deepgram/aura-1",
        {
          text,
          speaker: "luna",
          encoding: "mulaw",
          container: "none",
          sample_rate: 8000,
        },
        { returnRawResponse: true },
      ) as Response;

      const reader = response.body?.getReader();
      if (!reader) throw new Error("tts_audio_unavailable");

      while (this.speaking && !this.closed) {
        const { done, value } = await reader.read();
        if (done) break;

        for (let offset = 0; offset < value.length && this.speaking; offset += 160) {
          const frame = value.subarray(offset, Math.min(offset + 160, value.length));

          this.socket?.send(JSON.stringify({
            event: "media",
            streamSid: this.streamSid,
            media: { payload: encodeBase64(frame) },
          }));

          await new Promise((resolve) => setTimeout(resolve, 20));
        }
      }

      reader.releaseLock();
    } catch {
      // Preserve the call; log a sanitized error code through approved telemetry.
    } finally {
      this.speaking = false;
    }
  }

  private saveTranscript(role: "user" | "assistant", text: string): void {
    this.state.storage.sql.exec(
      "INSERT INTO transcript (role, text, ts) VALUES (?, ?, ?)",
      role,
      text.slice(0, 4000),
      Date.now(),
    );
  }

  private async finish(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.generation += 1;

    try {
      this.stt?.close();
    } catch {
      // Socket may already be closed.
    }

    this.stt = undefined;
    await this.state.storage.deleteAlarm();

    try {
      this.socket?.close(1000, "Call ended");
    } catch {
      // Socket may already be closed.
    }
  }
}

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);

  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }

  return bytes;
}

function encodeBase64(bytes: Uint8Array): string {
  let binary = "";

  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + 0x8000, bytes.length)));
  }

  return btoa(binary);
}
