import { Agent, type Connection, type ConnectionContext, type WSMessage } from "agents";
import type { Env } from "./types";
import { escapeXml, parseStreamPath, verifyStreamToken } from "./twilio";

// ---- Model contracts: verify against the live Workers AI model schemas before production. ----
const LLM_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
const STT_MODEL = "@cf/deepgram/nova-3";
const TTS_MODEL = "@cf/deepgram/aura-1";
const STT_PARAMS = { encoding: "mulaw", sample_rate: "8000", interim_results: "true", vad_events: "true" };
const TTS_PARAMS = { speaker: "luna", encoding: "mulaw", container: "none", sample_rate: 8000 };
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
        await this.schedule(MAX_CALL_SECONDS, "hangup");
        try {
          await this.openStt(conn);
          await this.speak(conn, GREETING, ++this.turn);
        } catch (e) {
          await this.fallback("start failed");
        }
        break;
      }
      case "media": {
        if (this.stt && this.stt.readyState === WebSocket.OPEN && msg.media?.payload) {
          this.stt.send(base64ToBytes(msg.media.payload));
        }
        break;
      }
      case "stop":
        await this.finalize();
        break;
    }
  }

  async onClose() {
    await this.finalize();
  }

  /** Scheduled hard cap, also used by the fallback path. */
  async hangup() {
    await this.twilioUpdate("Status=completed");
    await this.finalize();
  }

  // ---------------- STT ----------------

  private async openStt(conn: Connection) {
    const resp: any = await (this.env.AI as any).run(STT_MODEL, STT_PARAMS, { websocket: true });
    const ws: WebSocket | undefined = resp?.webSocket;
    if (!ws) throw new Error("STT websocket unavailable");
    ws.accept();
    this.stt = ws;

    ws.addEventListener("message", (ev: MessageEvent) => {
      this.onSttEvent(conn, ev.data).catch(() => this.fallback("stt event failed"));
    });
    ws.addEventListener("close", () => {
      this.stt = undefined;
    });
  }

  private async onSttEvent(conn: Connection, data: unknown) {
    if (typeof data !== "string") return;
    const r = JSON.parse(data);

    // Barge-in: caller started talking while we are speaking.
    if (r.type === "SpeechStarted" && this.speaking && Date.now() - this.speakStartedAt > BARGE_IN_GUARD_MS) {
      this.turn++; // cancels any in-flight response
      this.speaking = false;
      conn.send(JSON.stringify({ event: "clear", streamSid: this.state.streamSid }));
      return;
    }

    const text: string | undefined = r.channel?.alternatives?.[0]?.transcript?.trim();
    if (r.is_final && r.speech_final && text) {
      this.record("user", text);
      this.history.push({ role: "user", content: text });
      await this.respond(conn);
    }
  }

  // ---------------- LLM -> TTS ----------------

  private async respond(conn: Connection) {
    const turn = ++this.turn;
    const stream = (await (this.env.AI as any).run(LLM_MODEL, {
      messages: [{ role: "system", content: SYSTEM_PROMPT }, ...this.history.slice(-20)],
      stream: true,
      max_tokens: 200,
    })) as ReadableStream<Uint8Array>;

    let buf = "";
    let full = "";
    for await (const delta of sseText(stream)) {
      if (turn !== this.turn) {
        await stream.cancel().catch(() => {});
        return;
      }
      buf += delta;
      full += delta;
      let m: RegExpExecArray | null;
      while ((m = SENTENCE_END.exec(buf))) {
        const sentence = buf.slice(0, m.index + 1).trim();
        buf = buf.slice(m.index + m[0].length);
        if (sentence) await this.speak(conn, sentence, turn);
        if (turn !== this.turn) return;
      }
    }
    if (buf.trim() && turn === this.turn) await this.speak(conn, buf.trim(), turn);

    if (full.trim() && turn === this.turn) {
      this.history.push({ role: "assistant", content: full.trim() });
      this.record("assistant", full.trim());
    }
  }

  private async speak(conn: Connection, text: string, turn: number) {
    if (turn !== this.turn) return;
    try {
      const res: any = await (this.env.AI as any).run(
        TTS_MODEL,
        { text, ...TTS_PARAMS },
        { returnRawResponse: true },
      );
      const bytes = new Uint8Array(await res.arrayBuffer());
      if (bytes.length === 0) throw new Error("empty TTS audio");
      if (turn !== this.turn) return;

      this.speaking = true;
      this.speakStartedAt = Date.now();
      for (let o = 0; o < bytes.length; o += FRAME_BYTES) {
        conn.send(
          JSON.stringify({
            event: "media",
            streamSid: this.state.streamSid,
            media: { payload: bytesToBase64(bytes.subarray(o, o + FRAME_BYTES)) },
          }),
        );
      }
      conn.send(
        JSON.stringify({ event: "mark", streamSid: this.state.streamSid, mark: { name: crypto.randomUUID() } }),
      );
    } catch {
      await this.fallback("tts failed");
    }
  }

  // ---------------- Tools (disabled until the platform API boundary is confirmed) ----------------

  // this.state.tenantId / businessId come from the signed stream path (resolved by the platform from the
  // dialed number). Platform calls must send them as server-side scope; never take tenant/business
  // identifiers from caller speech. Booking, message intake, transfer and call-log postback stay OFF
  // until tenant-authorized platform endpoints exist and are tested.

  // ---------------- Fallback, persistence, cleanup ----------------

  private async fallback(_reason: string) {
    this.turn++;
    this.speaking = false;
    await this.twilioUpdate(
      `Twiml=${encodeURIComponent(FALLBACK_TWIML("Sorry, we are having technical trouble. Please try again shortly."))}`,
    );
    await this.finalize();
  }

  private async twilioUpdate(body: string) {
    const sid = this.state.callSid;
    if (!sid) return;
    try {
      await fetch(
        `https://api.twilio.com/2010-04-01/Accounts/${this.env.TWILIO_ACCOUNT_SID}/Calls/${sid}.json`,
        {
          method: "POST",
          headers: {
            Authorization: "Basic " + btoa(`${this.env.TWILIO_ACCOUNT_SID}:${this.env.TWILIO_AUTH_TOKEN}`),
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body,
        },
      );
    } catch {
      // Nothing more we can do; the call will end when Twilio's stream closes.
    }
  }

  private record(role: "user" | "assistant", text: string) {
    this.sql`INSERT INTO transcript (role, text, ts) VALUES (${role}, ${text}, ${Date.now()})`;
  }

  private async finalize() {
    if (this.finalized) return;
    this.finalized = true;
    this.turn++;
    this.speaking = false;
    try {
      this.stt?.close();
    } catch {}
    this.stt = undefined;
    // TODO(after retention policy is decided): post summary to the platform call-log endpoint,
    // then delete this call's transcript rows. Do not log transcripts or phone numbers.
  }
}

// ---------------- helpers ----------------

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

/** Parses a Workers AI server-sent-event stream into text deltas. */
async function* sseText(stream: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = stream.getReader();
  const dec = new TextDecoder();
  let pending = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      pending += dec.decode(value, { stream: true });
      let nl: number;
      while ((nl = pending.indexOf("\n")) !== -1) {
        const line = pending.slice(0, nl).trim();
        pending = pending.slice(nl + 1);
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") return;
        try {
          const j = JSON.parse(payload);
          if (typeof j.response === "string" && j.response) yield j.response;
        } catch {
          // ignore malformed chunk
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
