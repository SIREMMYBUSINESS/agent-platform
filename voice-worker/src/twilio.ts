// Twilio helpers: webhook signature validation, per-call stream tokens, XML escaping.
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
  const sorted = [...pairs].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
  let data = url;
  for (const [k, v] of sorted) data += k + v;
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

/** Call-specific token for the media stream URL path: HMAC-SHA256(secret, `${callSid}.${exp}`). */
export async function makeStreamToken(secret: string, callSid: string, exp: number): Promise<string> {
  return toHex(await hmac("SHA-256", secret, `${callSid}.${exp}`));
}

export async function verifyStreamToken(
  secret: string,
  callSid: string,
  expRaw: string,
  token: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<boolean> {
  if (!CALL_SID_RE.test(callSid)) return false;
  if (!/^\d{9,12}$/.test(expRaw)) return false;
  const exp = Number(expRaw);
  if (exp < nowSeconds) return false;
  return safeEqual(await makeStreamToken(secret, callSid, exp), token);
}

export function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}
