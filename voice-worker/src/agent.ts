import { Agent, type Connection, type ConnectionContext, type WSMessage } from "agents";
import type { Env } from "./types";
import { escapeXml, parseStreamPath, verifyStreamToken } from "./twilio";
import { decodeMulaw8kToLinear16k } from "./audio";
import {
  closeSocketSafely,
  FinalTranscriptDeduplicator,
  getCompletedTranscript,
  isCurrentSocketGeneration,
  isNova3SpeechStarted,
  openNova3WebSocket,
  parseNova3Message,
} from "./stt";

// ---- Model contracts: verify against the live Workers AI model schemas before production. ----
const LLM_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
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

function logStreamFailure(stage: string, error: unknown) {
  const err = error instanceof Error ? error : new Error(String(error));
  console.warn(JSON.stringify({
    event: "stream_stage_failed",
    stage,
    error_name: err.name,
    error_message: err.message.slice(0, 200),
  }));
}

export class ReceptionistAgent extends Agent<Env, CallState> {
  // Keep the DO resident for the life of the call (it holds an outbound STT socket).
  static options = { hibernate: false };

  initialState: CallState = {};

  private stt?: WebSocket;
  private sttMessageQueue: Promise<void> = Promise.resolve();
  private utteranceQueue: Promise<void> = Promise.resolve();
  private finalTranscriptDeduplicator = new FinalTranscriptDeduplicator();
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
          console.log(JSON.stringify({ event: "stream_stt_open_ok" }));
        } catch (e) {
          logStreamFailure("stt_open", e);
          await this.fallback(conn);
          break;
        }
        try {
          await this.say(conn, GREETING);
        } catch (e) {
          logStreamFailure("greeting", e);
          await this.fallback(conn);
        }
        break;
      }
      case "media": {
        if (this.stt && this.stt.readyState === WebSocket.OPEN && msg.media?.payload) {
          const mulaw = decodeBase64(msg.media.payload);
          const pcm16 = decodeMulaw8kToLinear16k(mulaw);
          this.stt.send(pcm16.buffer.slice(pcm16.byteOffset, pcm16.byteOffset + pcm16.byteLength) as ArrayBuffer);
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
    const socket = await openNova3WebSocket(this.env);
    socket.binaryType = "arraybuffer";
    socket.accept();
    this.stt = socket;
    const generation = ++this.socketGeneration;

    socket.addEventListener("message", (message) => {
      if (!isCurrentSocketGeneration(this.socketGeneration, generation)) return;
      this.sttMessageQueue = this.sttMessageQueue
        .then(async () => {
          if (!isCurrentSocketGeneration(this.socketGeneration, generation)) return;
          await this.handleSttMessage(conn, message.data, generation);
        })
        .catch((error) => {
          if (!isCurrentSocketGeneration(this.socketGeneration, generation)) return;
          logStreamFailure("stt_message", error);
          return this.fallback(conn);
        });
    });

    socket.addEventListener("close", () => {
      if (!isCurrentSocketGeneration(this.socketGeneration, generation)) return;
      this.socketGeneration++;
      if (this.stt === socket) this.stt = undefined;
      if (!this.ending) void this.fallback(conn);
    });

    socket.addEventListener("error", () => {
      if (!isCurrentSocketGeneration(this.socketGeneration, generation)) return;
      this.socketGeneration++;
      if (this.stt === socket) this.stt = undefined;
      closeSocketSafely(socket);
      logStreamFailure("stt_socket", new Error("Nova-3 WebSocket error"));
      if (!this.ending) void this.fallback(conn);
    });
  }

  private async handleSttMessage(conn: Connection, data: unknown, generation: number) {
    const result = await parseNova3Message(data);
    if (!result || !isCurrentSocketGeneration(this.socketGeneration, generation)) return;

    if (
      isNova3SpeechStarted(result) &&
      this.state.speaking &&
      Date.now() - this.speakStartedAt >= BARGE_IN_GUARD_MS
    ) {
      conn.send(JSON.stringify({ event: "clear", streamSid: this.state.streamSid }));
      this.setState({ ...this.state, speaking: false });
    }

    const completed = getCompletedTranscript(result);
    if (!completed || !this.finalTranscriptDeduplicator.shouldAccept(completed)) return;

    // Serialize LLM/TTS turns independently of provider event parsing so SpeechStarted
    // events can still interrupt audio while a previous response is being generated.
    this.utteranceQueue = this.utteranceQueue
      .then(async () => {
        if (!isCurrentSocketGeneration(this.socketGeneration, generation)) return;
        await this.handleUtterance(conn, completed.text);
      })
      .catch((error) => {
        if (!isCurrentSocketGeneration(this.socketGeneration, generation)) return;
        logStreamFailure("stt_turn", error);
        return this.fallback(conn);
      });
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
    this.speakStartedAt = Date.now();
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
    } catch (e) {
      logStreamFailure("tts", e);
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
    const socket = this.stt;
    this.stt = undefined;
    closeSocketSafely(socket);
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
