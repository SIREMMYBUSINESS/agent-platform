/** Twilio's signature uses the exact externally-visible URL and sorted form fields. */
export async function verifyTwilioSignature(
  req: Request,
  form: FormData,
  authToken: string,
  publicOrigin: string,
): Promise<boolean> {
  const supplied = req.headers.get("X-Twilio-Signature") ?? "";
  if (!supplied || !authToken || !publicOrigin) return false;
  const requestUrl = new URL(req.url);
  const publicUrl = new URL(requestUrl.pathname + requestUrl.search, publicOrigin).toString();
  let data = publicUrl;
  for (const key of [...new Set([...form.keys()])].sort()) {
    const values = form.getAll(key).map((value) => typeof value === "string" ? value : "");
    for (const value of values) data += key + value;
  }
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(authToken), { name: "HMAC", hash: "SHA-1" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  return constantTimeEqual(supplied, expected);
}

/** A call-specific bearer for the media URL; the CallSid alone is not authorization. */
export async function signStreamToken(callSid: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`voice-stream:${callSid}`));
  return base64Url(new Uint8Array(mac));
}

function constantTimeEqual(left: string, right: string): boolean {
  const a = new TextEncoder().encode(left);
  const b = new TextEncoder().encode(right);
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a[i] ?? 0) ^ (b[i] ?? 0);
  return diff === 0;
}

function base64Url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}
