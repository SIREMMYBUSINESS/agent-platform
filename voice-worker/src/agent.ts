import { Agent, type Connection } from "agents";
import type { Env } from "./index";

type CallState = {
  streamSid?: string;
  caller?: string;
  speaking: boolean;
  startedAt?: number;
  turns: number;
};
type Turn = { role: "user" | "assistant"; content: string };
type TwilioEvent = {
  event?: string;
  start?: { streamSid?: string; callSid?: string; customParameters?: Record<string, string> };
  media?: { payload?: string };
  streamSid?: string;
};

const SYSTEM_PROMPT = `You are a concise, friendly phone receptionist. Keep spoken replies to at most two short sentences. Collect the caller's name and reason, help request an appointment, or take a message. Never invent availability or claim an action succeeded unless a tool confirms it. For medical or other emergencies, advise contacting local emergency services and offer a human transfer.`;

export class ReceptionistAgent extends Agent<Env, CallState> {
  initialState: CallState = { speaking: false, turns: 0 };
  private history: Turn[] = [];
  private stt?: WebSocket;
  private socketGeneration = 0;
  private activeConnection?: Connection;
  private ending = false;

  async onStart() {
    this.sql`CREATE TABLE IF NOT EXISTS transcript (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
      text TEXT NOT NULL,
      ts INTEGER NOT NULL
    )`;
    const maxSeconds = Math.max(60, Math.min(7200, Number(this.env.MAX_CALL_SECONDS ?? 1800)));
    await this.schedule(maxSeconds, "max_call_duration");
  }

  async onMessage(conn: Connection, raw: string | ArrayBuffer) {
    this.activeConnection = conn;
    let event: TwilioEvent;
    try {
      event = JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw));
    } catch {
      return;
    }
    if (event.event === "start" && event.start?.streamSid) {
      this.setState({ ...this.state, streamSid: event.start.streamSid, startedAt: Date.now() });
      await this.openStt(conn);
      await this.say(conn, "Thanks for calling. How can I help you today?");
    } else if (event.event === "media" && event.media?.payload) {
      const payload = decodeBase64(event.media.payload);
      if (this.stt?.readyState === WebSocket.OPEN) this.stt.send(payload);
    } else if (event.event === "stop") {
      await this.finalizeCall();
    }
  }

  async onClose() {
    await this.finalizeCall();
  }

  async onAlarm(alarmInfo: { name?: string }) {
    if (alarmInfo.name === "max_call_duration") await this.finalizeCall();
  }

  private async openStt(conn: Connection) {
    // Workers AI partner model supports streaming via its websocket response.
    const result = await this.env.AI.run(
      "@cf/deepgram/nova-3" as keyof AiModels,
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
    if (this.ending || this.state.turns >= 100) return;
    this.history.push({ role: "user", content: text });
    this.saveTranscript("user", text);
    this.setState({ ...this.state, turns: this.state.turns + 1 });
    const response = await this.env.AI.run(
      "@cf/meta/llama-3.3-70b-instruct-fp8-fast" as keyof AiModels,
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
        "@cf/deepgram/aura-1" as keyof AiModels,
        { text, speaker: "luna", encoding: "mulaw", container: "none", sample_rate: 8000 } as never,
        { returnRawResponse: true } as never,
      ) as Response;
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

  private async finalizeCall() {
    if (this.ending) return;
    this.ending = true;
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
