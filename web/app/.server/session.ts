const COOKIE_NAME = "__Host-agent_session";
const SESSION_TTL_SECONDS = 60 * 60;
const SESSION_TTL_MS = SESSION_TTL_SECONDS * 1000;

type Session = { apiKey: string; expiresAt: number };

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

function decodeBase64(value: string): Uint8Array {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
  const raw = atob(padded);
  return Uint8Array.from(raw, (char) => char.charCodeAt(0));
}
function encodeBase64Url(value: Uint8Array): string {
  let raw = "";
  for (const byte of value) raw += String.fromCharCode(byte);
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}
async function importSessionKey(secret: string): Promise<CryptoKey> {
  const bytes = decodeBase64(secret);
  if (bytes.byteLength !== 32) throw new Error("SESSION_ENCRYPTION_KEY must decode to 32 bytes.");
  return crypto.subtle.importKey("raw", toArrayBuffer(bytes), { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}
function cookieValue(request: Request): string | null {
  const header = request.headers.get("Cookie");
  if (!header) return null;
  for (const entry of header.split(";")) {
    const [name, ...parts] = entry.trim().split("=");
    if (name === COOKIE_NAME) return parts.join("=") || null;
  }
  return null;
}

export async function createSessionCookie(apiKey: string, secret: string): Promise<string> {
  const key = await importSessionKey(secret);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const session: Session = { apiKey, expiresAt: Date.now() + SESSION_TTL_MS };
  const plaintext = new TextEncoder().encode(JSON.stringify(session));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: toArrayBuffer(iv) }, key, toArrayBuffer(plaintext),
  ));
  const value = encodeBase64Url(iv) + "." + encodeBase64Url(ciphertext);
  return COOKIE_NAME + "=" + value + "; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=" + SESSION_TTL_SECONDS;
}

export async function readSessionApiKey(request: Request, secret: string): Promise<string | null> {
  const value = cookieValue(request);
  if (!value || !secret) return null;
  try {
    const [encodedIv, encodedCiphertext, extra] = value.split(".");
    if (!encodedIv || !encodedCiphertext || extra) return null;
    const iv = decodeBase64(encodedIv);
    if (iv.byteLength !== 12) return null;
    const key = await importSessionKey(secret);
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: toArrayBuffer(iv) },
      key,
      toArrayBuffer(decodeBase64(encodedCiphertext)),
    );
    const session = JSON.parse(new TextDecoder().decode(plaintext)) as Session;
    if (typeof session.apiKey !== "string"
      || !/^ap_[A-Za-z0-9_-]{43}$/.test(session.apiKey)
      || typeof session.expiresAt !== "number"
      || session.expiresAt <= Date.now()) return null;
    return session.apiKey;
  } catch {
    return null;
  }
}
