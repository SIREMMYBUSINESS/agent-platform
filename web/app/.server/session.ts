const SESSION_COOKIE_NAME = "__Host-agent_session";
const LOGIN_COOKIE_NAME = "__Host-agent_login";
const SESSION_TTL_SECONDS = 60 * 60 * 12;
const LOGIN_TICKET_TTL_SECONDS = 60 * 5;

type EncryptedPayload = { value: string; expiresAt: number };

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

function cookieValue(request: Request, cookieName: string): string | null {
  const header = request.headers.get("Cookie");
  if (!header) return null;
  for (const entry of header.split(";")) {
    const [name, ...parts] = entry.trim().split("=");
    if (name === cookieName) return parts.join("=") || null;
  }
  return null;
}

async function createEncryptedCookie(
  cookieName: string, value: string, secret: string, maxAge: number,
): Promise<string> {
  const key = await importSessionKey(secret);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const payload: EncryptedPayload = { value, expiresAt: Date.now() + maxAge * 1000 };
  const plaintext = new TextEncoder().encode(JSON.stringify(payload));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: toArrayBuffer(iv) }, key, toArrayBuffer(plaintext),
  ));
  const encoded = encodeBase64Url(iv) + "." + encodeBase64Url(ciphertext);
  return cookieName + "=" + encoded + "; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=" + maxAge;
}

async function readEncryptedCookie(
  request: Request, cookieName: string, secret: string,
): Promise<string | null> {
  const value = cookieValue(request, cookieName);
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
    const payload = JSON.parse(new TextDecoder().decode(plaintext)) as EncryptedPayload;
    if (typeof payload.value !== "string"
      || typeof payload.expiresAt !== "number"
      || payload.expiresAt <= Date.now()) return null;
    return payload.value;
  } catch {
    return null;
  }
}

export async function createSessionCookie(
  credential: string, secret: string, expiresAt?: number,
): Promise<string> {
  if (!/^(ap|ws)_[A-Za-z0-9_-]{43}$/.test(credential)) {
    throw new Error("Workspace credential format is invalid.");
  }
  const ttl = expiresAt === undefined
    ? SESSION_TTL_SECONDS
    : Math.max(1, Math.min(SESSION_TTL_SECONDS, Math.floor((expiresAt - Date.now()) / 1000)));
  return createEncryptedCookie(SESSION_COOKIE_NAME, credential, secret, ttl);
}

export async function readSessionCredential(request: Request, secret: string): Promise<string | null> {
  const value = await readEncryptedCookie(request, SESSION_COOKIE_NAME, secret);
  return value && /^(ap|ws)_[A-Za-z0-9_-]{43}$/.test(value) ? value : null;
}

export async function readSessionApiKey(request: Request, secret: string): Promise<string | null> {
  return readSessionCredential(request, secret);
}

export async function createPendingLoginCookie(ticket: string, secret: string): Promise<string> {
  if (!/^lt_[A-Za-z0-9_-]{43}$/.test(ticket)) throw new Error("Login ticket format is invalid.");
  return createEncryptedCookie(LOGIN_COOKIE_NAME, ticket, secret, LOGIN_TICKET_TTL_SECONDS);
}

export async function readPendingLoginTicket(request: Request, secret: string): Promise<string | null> {
  const ticket = await readEncryptedCookie(request, LOGIN_COOKIE_NAME, secret);
  return ticket && /^lt_[A-Za-z0-9_-]{43}$/.test(ticket) ? ticket : null;
}

export function clearSessionCookie(): string {
  return SESSION_COOKIE_NAME + "=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0";
}

export function clearPendingLoginCookie(): string {
  return LOGIN_COOKIE_NAME + "=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0";
}
