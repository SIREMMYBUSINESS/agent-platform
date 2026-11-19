// Twilio helpers: webhook signature validation, per-call stream tokens, XML escaping.
// Uses only WebCrypto + TextEncoder so it runs in Workers and Node tests.

const encoder = new TextEncoder();

async function hmac(hash: "SHA-1" | "SHA-256", secret: string, data: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(data)));
}

function toBase64(bytes: Uint8Array): string {
  let value = "";
  for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value);
}

function toHex(bytes: Uint8Array): string {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Constant-time comparison for equal-length strings. */
export function safeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let i = 0; i < left.length; i++) difference |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return difference === 0;
}

/** Twilio signature: base64(HMAC-SHA1(authToken, URL + key/value pairs sorted by key)). */
export async function twilioSignature(
  authToken: string,
  url: string,
  pairs: Array<[string, string]>,
): Promise<string> {
  const sorted = [...pairs].sort((a, b) =>
    a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0,
  );
  let data = url;
  for (const [key, value] of sorted) data += key + value;
  return toBase64(await hmac("SHA-1", authToken, data));
}

export async function validTwilioSignature(
  authToken: string,
  url: string,
  pairs: Array<[string, string]>,
  supplied: string | null,
): Promise<boolean> {
  if (!supplied) return false;
  return safeEqual(await twilioSignature(authToken, url, pairs), supplied);
}

export const CALL_SID_RE = /^CA[0-9a-f]{32}$/i;

/** Call-specific token used in the Twilio Media Streams path. */
export async function makeStreamToken(secret: string, callSid: string, expiresAt: number): Promise<string> {
  return toHex(await hmac("SHA-256", secret, `${callSid}.${expiresAt}`));
}

export async function verifyStreamToken(
  secret: string,
  callSid: string,
  expiresAtRaw: string,
  token: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<boolean> {
  if (!CALL_SID_RE.test(callSid) || !/^\d{9,12}$/.test(expiresAtRaw) || !/^[0-9a-f]{64}$/.test(token)) return false;
  const expiresAt = Number(expiresAtRaw);
  if (expiresAt < nowSeconds || expiresAt > nowSeconds + 120) return false;
  return safeEqual(await makeStreamToken(secret, callSid, expiresAt), token);
}

export function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}
