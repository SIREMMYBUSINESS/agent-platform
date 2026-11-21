// Twilio helpers: webhook signature validation, tenant-bound stream tokens, XML escaping.
// Uses only WebCrypto + TextEncoder so it runs in Workers and in Node tests.

const enc = new TextEncoder();

async function hmac(hash: "SHA-1" | "SHA-256", secret: string, data: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(data)));
}

function toBase64(b: Uint8Array): string {
  let s = "";
  for (const x of b) s += String.fromCharCode(x);
  return btoa(s);
}

function toHex(b: Uint8Array): string {
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

/** Constant-time string comparison. */
export function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Twilio signature: base64(HMAC-SHA1(authToken, url + each key+value sorted by key)). */
export async function twilioSignature(
  authToken: string,
  url: string,
  pairs: Array<[string, string]>,
): Promise<string> {
  // Twilio's SDK normalizes repeated form keys as arrays: unique values sorted
  // lexicographically, then each key/value pair appended. FormData can expose
  // repeated keys, so normalize them the same way rather than sorting raw pairs.
  const grouped = new Map<string, string[]>();
  for (const [key, value] of pairs) {
    const values = grouped.get(key);
    if (values) values.push(value);
    else grouped.set(key, [value]);
  }

  let data = url;
  for (const key of [...grouped.keys()].sort()) {
    const values = [...new Set(grouped.get(key)!)].sort();
    for (const value of values) data += key + value;
  }
  return toBase64(await hmac("SHA-1", authToken, data));
}

export async function validTwilioSignature(
  authToken: string,
  url: string,
  pairs: Array<[string, string]>,
  header: string | null,
): Promise<boolean> {
  if (!header) return false;
  return safeEqual(await twilioSignature(authToken, url, pairs), header);
}

export const CALL_SID_RE = /^CA[0-9a-f]{32}$/;
/** Tenant/business ids from the platform: opaque, URL-safe, bounded. */
export const SCOPE_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

export const STREAM_TOKEN_TTL_SECONDS = 120;
const MAX_TOKEN_WINDOW_SECONDS = 300;

export type StreamScope = {
  callSid: string;
  exp: number;
  tenantId: string;
  businessId: string;
};

function tokenPayload(s: StreamScope): string {
  return `${s.callSid}\n${s.tenantId}\n${s.businessId}\n${s.exp}`;
}

/** HMAC-SHA256 over callSid, tenant, business and expiry. Any change invalidates the token. */
export async function makeStreamToken(secret: string, scope: StreamScope): Promise<string> {
  return toHex(await hmac("SHA-256", secret, tokenPayload(scope)));
}

/** Path: /voice/stream/<callSid>/<exp>/<tenantId>/<businessId>/<token> */
export function streamPath(scope: StreamScope, token: string): string {
  return `/voice/stream/${scope.callSid}/${scope.exp}/${scope.tenantId}/${scope.businessId}/${token}`;
}

export function parseStreamPath(pathname: string): (StreamScope & { token: string }) | null {
  const m = pathname.match(/^\/voice\/stream\/([^/]+)\/(\d{9,12})\/([^/]+)\/([^/]+)\/([0-9a-f]{64})$/);
  if (!m) return null;
  const [, callSid, exp, tenantId, businessId, token] = m;
  if (!CALL_SID_RE.test(callSid) || !SCOPE_ID_RE.test(tenantId) || !SCOPE_ID_RE.test(businessId)) return null;
  return { callSid, exp: Number(exp), tenantId, businessId, token };
}

export async function verifyStreamToken(
  secret: string,
  parsed: StreamScope & { token: string },
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<boolean> {
  if (parsed.exp < nowSeconds) return false;
  if (parsed.exp > nowSeconds + MAX_TOKEN_WINDOW_SECONDS) return false;
  return safeEqual(await makeStreamToken(secret, parsed), parsed.token);
}

export function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}
