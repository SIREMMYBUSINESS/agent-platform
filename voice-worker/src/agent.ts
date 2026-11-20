import { Agent, type Connection, type ConnectionContext, type WSMessage } from "agents";
import type { Env } from "./types";
import { escapeXml, parseStreamPath, verifyStreamToken } from "./twilio";

// ---- Model contracts: verify against the live Workers AI model schemas before production. ----
const LLM_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
const STT_MODEL = "@cf/deepgram/nova-3";
const TTS_MODEL = "@cf/deepgram/aura-1";
// -----------------------------------------------------------------------------------------------

const MAX_CALL_SECONDS = 15 * 60;
const FRAME_BYTES = 160; // 20 ms of 8 kHz mu-law
const BARGE_IN_GUARD_MS = 400; // ignore speech-start right after we begin speaking (line echo)
const SENTENCE_END = /[.!?]\s/;

const SYSTEM_PROMPT = `You are a polite phone receptionist.
Keep every reply to one or two short spoken sentences. No lists, no markdown.
Find out who is calling and why. You cannot book, transfer or look anything up yet:
offer to take a message and say a team member will follow up.
Never invent availability, prices or policies.
If the caller describes a medical or safety emergency, tell them to hang up and call their local emergency number immediately.`;

const GREETING = "Thanks for calling. How can I help you today?";
const FALLBACK_TWIML = (msg: string) =>
  `<Response><Say>${escapeXml(msg)}</Say><Hangup/></Response>`;

// Tenant scope is set only from the signed stream path (verified in onConnect), never from caller input.
type CallState = {
  callSid?: string;
  tenantId?: string;
  businessId?: string;
  streamSid?: string;
  speaking?: boolean;
  turns?: number;
};

type ChatMsg = { role: "system" | "user" | "assistant"; content: string };

export class ReceptionistAgent extends Agent<Env, CallState> {
  // Keep the DO resident for the life of the call (it holds an outbound STT socket).
  static options = { hibernate: false };

  initialState: CallState = {};

  private stt?: WebSocket;
  private history: ChatMsg[] = [];
  private turn = 0;
  private speaking = false;
  private speakStartedAt = 0;
  private finalized = false;
  private ending = false;
  private socketGeneration = 0;
  private started = false;

  async onStart() {
    this.sql`CREATE TABLE IF NOT EXISTS transcript (
      id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, text TEXT NOT NULL, ts INTEGER NOT NULL)`;
  }

  /** Defense in depth: the Worker already verified the token, the Durable Object checks it again. */
  async onConnect(conn: Connection, ctx: ConnectionContext) {
    const parsed = parseStreamPath(new URL(ctx.request.url).pathname);
    if (!parsed || !(await verifyStreamToken(this.env.STREAM_SECRET, parsed))) {
      conn.close(1008, "unauthorized");
      return;
    }
    this.setState({ callSid: parsed.callSid, tenantId: parsed.tenantId, businessId: parsed.businessId });
  }

  async onMessage(conn: Connection, message: WSMessage) {
    if (typeof message !== "string") return;
    let msg: any;
    try {
      msg = JSON.parse(message);
    } catch {
      return;
    }

    switch (msg.event) {
      case "start": {
        // Only accept a start for the call this socket was authorized for, and only once.
        if (this.started || !this.state.callSid || msg.start?.callSid !== this.state.callSid) {
          conn.close(1008, "unauthorized");
          return;
        }
        this.started = true;
        this.setState({ ...this.state, streamSid: msg.start?.streamSid });
        await this.schedule(MAX_CALL_SECONDS, "hangup", {});
        try {
          await this.openStt(conn);
          await this.say(conn, GREETING);
        } catch (e) {
          await this.fallback(conn);
        }
        break;
      }
      case "media": {
        if (this.stt && this.stt.readyState === WebSocket.OPEN && msg.media?.payload) {
          const bytes = decodeBase64(msg.media.payload);
          this.stt.send(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
        }
        break;
      }
      case "stop":
        await this.finalizeCall();
        break;
    }
  }

  async onClose() {
    await this.finalizeCall();
  }


  private async openStt(conn: Connection) {
    // Workers AI partner model supports streaming via its websocket response.
    const result = await this.env.AI.run(
      STT_MODEL as keyof AiModels,
      { encoding: "mulaw", sample_rate: 8000, interim_results: true, vad_events: true } as never,
      { websocket: true } as never,
    ) as { webSocket?: WebSocket };
    const socket = result.webSocket;
    if (!socket) throw new Error("Workers AI STT did not return a websocket");
    socket.accept();
    this.stt = socket;
    const generation = ++this.socketGeneration;
    socket.addEventListener("message", (message) => {
      if (generation !== this.socketGeneration) return;
      void this.handleSttMessage(conn, message.data).catch(() => this.fallback(conn));
    });
  }

  private async handleSttMessage(conn: Connection, data: unknown) {
    let result: Record<string, any>;
    try { result = JSON.parse(typeof data === "string" ? data : new TextDecoder().decode(data as ArrayBuffer)); }
    catch { return; }
    if ((result.type === "SpeechStarted" || result.event === "SpeechStarted") && this.state.speaking) {
      conn.send(JSON.stringify({ event: "clear", streamSid: this.state.streamSid }));
      this.setState({ ...this.state, speaking: false });
    }
    const text = result.channel?.alternatives?.[0]?.transcript;
    if (result.is_final && result.speech_final && typeof text === "string" && text.trim()) {
      await this.handleUtterance(conn, text.trim());
    }
  }

  private async handleUtterance(conn: Connection, text: string) {
    const turns = this.state.turns ?? 0;
    if (this.ending || turns >= 100) return;
    this.history.push({ role: "user", content: text });
    this.saveTranscript("user", text);
    this.setState({ ...this.state, turns: turns + 1 });
    const response = await this.env.AI.run(
      LLM_MODEL as keyof AiModels,
      {
        messages: [{ role: "system", content: SYSTEM_PROMPT }, ...this.history.slice(-20)],
        max_tokens: 180,
        temperature: 0.2,
      } as never,
    ) as { response?: string };
    const reply = response.response?.trim();
    if (!reply) return this.fallback(conn);
    this.history.push({ role: "assistant", content: reply });
    this.saveTranscript("assistant", reply);
    await this.say(conn, reply);
  }

  private async say(conn: Connection, text: string) {
    if (this.ending || !this.state.streamSid) return;
    this.setState({ ...this.state, speaking: true });
    try {
      const audio = await this.env.AI.run(
        TTS_MODEL as keyof AiModels,
        { text, speaker: "luna", encoding: "mulaw", container: "none", sample_rate: 8000 } as never,
        { returnRawResponse: true } as never,
      ) as unknown as Response;
      const reader = audio.body?.getReader();
      if (!reader) throw new Error("Workers AI TTS returned no audio body");
      while (this.state.speaking && !this.ending) {
        const { done, value } = await reader.read();
        if (done) break;
        // Twilio Media Streams expects outbound audio payloads in small μ-law chunks.
        for (let offset = 0; offset < value.length && this.state.speaking; offset += 160) {
          const frame = value.subarray(offset, Math.min(offset + 160, value.length));
          conn.send(JSON.stringify({
            event: "media", streamSid: this.state.streamSid,
            media: { payload: encodeBase64(frame) },
          }));
        }
      }
      reader.releaseLock();
    } catch {
      await this.fallback(conn);
    } finally {
      this.setState({ ...this.state, speaking: false });
    }
  }

  private async fallback(conn: Connection) {
    if (this.ending || !this.state.streamSid) return;
    // Keep the call alive with provider-generated audio only as a last resort.
    conn.send(JSON.stringify({ event: "clear", streamSid: this.state.streamSid }));
  }

  private saveTranscript(role: string, text: string) {
    this.sql`INSERT INTO transcript (role, text, ts) VALUES (${role}, ${text.slice(0, 4000)}, ${Date.now()})`;
  }

  async hangup() {
    await this.finalizeCall();
  }

  private async finalizeCall() {
    if (this.ending) return;
    this.ending = true;
    this.finalized = true;
    this.socketGeneration++;
    try { this.stt?.close(); } catch { /* already closed */ }
    this.stt = undefined;
  }
}

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function encodeBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, Math.min(i + 0x8000, bytes.length)));
  }
  return btoa(binary);
}
