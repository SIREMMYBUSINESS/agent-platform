import { DurableObject } from "cloudflare:workers";
import type { Env } from "./types";
import { escapeXml, makeStreamToken, streamPath, STREAM_TOKEN_TTL_SECONDS } from "./twilio";

const WINDOW_MS = 60_000;
const ACCOUNT_LIMIT = 60;
const NUMBER_CACHE_TTL_MS = 30_000;
const CALL_RESULT_TTL_SECONDS = 24 * 60 * 60;
const DEFAULT_BUSINESS_LIMIT = 10;
const MAX_BODY_BYTES = 2_000;
const CALL_SID_RE = /^CA[0-9a-f]{32}$/;
const ACCOUNT_SID_RE = /^AC[0-9a-f]{32}$/i;
const PHONE_RE = /^\+[1-9][0-9]{6,14}$/;
const SCOPE_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

type AdmissionInput = { accountSid: string; callSid: string; to: string };
type Scope = { tenantId: string; businessId: string; businessLimit: number };
type CachedScope = Scope & { expiresAt: number };
type StoredCall = { xml: string; expiresAt: number };

const twiml = (body: string) => new Response(
  `<?xml version="1.0" encoding="UTF-8"?>\n${body}`,
  { status: 200, headers: { "Content-Type": "text/xml; charset=utf-8", "Cache-Control": "no-store" } },
);
const busy = () => twiml("<Response><Say>We are busy right now. Please try again later.</Say><Hangup/></Response>");
const unavailable = () => twiml("<Response><Say>We are unable to connect your call right now. Please try again later.</Say><Hangup/></Response>");
const rawXml = (body: string) => new Response(body, {
  status: 200, headers: { "Content-Type": "text/xml; charset=utf-8", "Cache-Control": "no-store" },
});

export class VoiceAdmission extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
  }

  async fetch(request: Request): Promise<Response> {
    if (request.method !== "POST" || new URL(request.url).pathname !== "/admit") {
      return new Response("Not Found", { status: 404 });
    }
    const length = Number(request.headers.get("content-length") ?? "0");
    if (length > MAX_BODY_BYTES) return new Response("Payload Too Large", { status: 413 });
    let input: AdmissionInput;
    try {
      input = await request.json() as AdmissionInput;
    } catch {
      return new Response("Bad Request", { status: 400 });
    }
    if (!input || !ACCOUNT_SID_RE.test(input.accountSid) ||
        !CALL_SID_RE.test(input.callSid) || !PHONE_RE.test(input.to)) {
      return new Response("Bad Request", { status: 400 });
    }

    // Serialize the complete decision, including the platform lookup. Concurrent Twilio
    // retries for the same CallSid therefore observe the first stored response.
    return this.ctx.blockConcurrencyWhile(() => this.admit(input));
  }

  private async admit(input: AdmissionInput): Promise<Response> {
    const now = Date.now();
    const callKey = `call:${input.callSid}`;
    const previous = await this.ctx.storage.get<StoredCall>(callKey);
    if (previous && previous.expiresAt > now) return rawXml(previous.xml);
    if (previous) await this.ctx.storage.delete(callKey);

    // Account-wide ceiling is checked before any number-to-business resolution.
    const accountTimes = await this.recentTimes("account", now);
    if (accountTimes.length >= ACCOUNT_LIMIT) {
      return this.remember(callKey, busy(), now);
    }
    accountTimes.push(now);
    await this.ctx.storage.put("account", accountTimes);

    const scope = await this.resolveCached(input.to);
    if (!scope) return this.remember(callKey, unavailable(), now);

    const businessKey = `business:${scope.tenantId}:${scope.businessId}`;
    const businessTimes = await this.recentTimes(businessKey, now);
    if (businessTimes.length >= scope.businessLimit) {
      return this.remember(callKey, busy(), now);
    }
    businessTimes.push(now);
    await this.ctx.storage.put(businessKey, businessTimes);

    const exp = Math.floor(now / 1000) + STREAM_TOKEN_TTL_SECONDS;
    const stream = { callSid: input.callSid, exp, tenantId: scope.tenantId, businessId: scope.businessId };
    const token = await makeStreamToken(this.env.STREAM_SECRET, stream);
    const wsOrigin = this.env.PUBLIC_ORIGIN.replace(/^https:/, "wss:");
    const response = twiml(
      `<Response><Connect><Stream url="${escapeXml(`${wsOrigin}${streamPath(stream, token)}`)}"/></Connect></Response>`,
    );
    return this.remember(callKey, response, now);
  }

  private async recentTimes(key: string, now: number): Promise<number[]> {
    const values = await this.ctx.storage.get<number[]>(key) ?? [];
    const recent = values.filter((time) => Number.isFinite(time) && time > now - WINDOW_MS);
    if (recent.length !== values.length) await this.ctx.storage.put(key, recent);
    return recent;
  }

  private async resolveCached(to: string): Promise<Scope | null> {
    const key = `number:${to}`;
    const now = Date.now();
    const cached = await this.ctx.storage.get<CachedScope>(key);
    if (cached && cached.expiresAt > now) return cached;
    if (cached) await this.ctx.storage.delete(key);

    let response: Response;
    try {
      const url = new URL("/voice/resolve-number", this.env.PLATFORM_API_URL);
      url.searchParams.set("to", to);
      response = await fetch(url.toString(), {
        headers: { Authorization: `Bearer ${this.env.PLATFORM_API_KEY}`, Accept: "application/json" },
        signal: AbortSignal.timeout(3000),
      });
    } catch (error) {
      console.warn(JSON.stringify({
        event: "voice_number_lookup_failed",
        reason: error instanceof Error && error.name === "TimeoutError" ? "timeout" : "network_error",
      }));
      return null;
    }
    if (!response.ok) {
      console.warn(JSON.stringify({ event: "voice_number_lookup_failed", reason: "http_error", http_status: response.status }));
      return null;
    }
    let body: { tenant_id?: unknown; business_id?: unknown; voice_calls_per_minute?: unknown };
    try {
      body = await response.json() as typeof body;
    } catch {
      console.warn(JSON.stringify({ event: "voice_number_lookup_failed", reason: "invalid_json" }));
      return null;
    }
    if (typeof body.tenant_id !== "string" || typeof body.business_id !== "string" ||
        !SCOPE_ID_RE.test(body.tenant_id) || !SCOPE_ID_RE.test(body.business_id)) {
      console.warn(JSON.stringify({ event: "voice_number_lookup_failed", reason: "invalid_scope" }));
      return null;
    }
    const configured = body.voice_calls_per_minute;
    const businessLimit = typeof configured === "number" && Number.isInteger(configured) &&
      configured >= 1 && configured <= ACCOUNT_LIMIT ? configured : DEFAULT_BUSINESS_LIMIT;
    const scope: CachedScope = {
      tenantId: body.tenant_id, businessId: body.business_id,
      businessLimit, expiresAt: now + NUMBER_CACHE_TTL_MS,
    };
    // Only successful, validated mappings are cached. Failures are never cached.
    await this.ctx.storage.put(key, scope);
    return scope;
  }

  async alarm(): Promise<void> {
    const entries = await this.ctx.storage.list<StoredCall>({ prefix: "call:" });
    const now = Date.now();
    let nextExpiry: number | undefined;
    for (const [key, record] of entries) {
      if (record.expiresAt <= now) await this.ctx.storage.delete(key);
      else nextExpiry = Math.min(nextExpiry ?? record.expiresAt, record.expiresAt);
    }
    if (nextExpiry !== undefined) await this.ctx.storage.setAlarm(nextExpiry);
  }

  private async scheduleCallCleanup(expiresAt: number): Promise<void> {
    const current = await this.ctx.storage.getAlarm();
    if (current === null || expiresAt < current) await this.ctx.storage.setAlarm(expiresAt);
  }

  private async remember(key: string, response: Response, now: number): Promise<Response> {
    const body = await response.text();
    await this.ctx.storage.put(key, {
      xml: body, expiresAt: now + CALL_RESULT_TTL_SECONDS * 1000,
    } satisfies StoredCall);
    await this.scheduleCallCleanup(now + CALL_RESULT_TTL_SECONDS * 1000);
    return rawXml(body);
  }
}
