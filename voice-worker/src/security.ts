/** Validate Twilio's HMAC-SHA1 signature against the configured canonical URL. */
export async function verifyTwilioSignature(
  request: Request,
  form: FormData,
  authToken: string,
  publicOrigin: string,
): Promise<boolean> {
  const supplied = request.headers.get("X-Twilio-Signature") ?? "";
  if (!supplied || !authToken || !publicOrigin) return false;

  const requestUrl = new URL(request.url);
  const canonicalUrl = new URL(
    requestUrl.pathname + requestUrl.search,
    publicOrigin,
  ).toString();

  let signedData = canonicalUrl;
  for (const key of [...new Set([...form.keys()])].sort()) {
    for (const value of form.getAll(key)) {
      signedData += key + (typeof value === "string" ? value : "");
    }
  }

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(authToken),
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"],
  );

  const digest = new Uint8Array(
    await crypto.subtle.sign(
      "HMAC",
      key,
      new TextEncoder().encode(signedData),
    ),
  );

  return constantTimeEqual(supplied, btoa(String.fromCharCode(...digest)));
}

/** Sign call and tenant scope for the Twilio Media Streams start event. */
export async function issueStreamToken(input: {
  callSid: string;
  tenantId: string;
  businessId: string;
  secret: string;
  nowSeconds: number;
}): Promise<string> {
  const expiresAt = input.nowSeconds + 300;
  const payload = `${input.callSid}\n${input.tenantId}\n${input.businessId}\n${expiresAt}`;

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(input.secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );

  const digest = new Uint8Array(
    await crypto.subtle.sign(
      "HMAC",
      key,
      new TextEncoder().encode(payload),
    ),
  );

  return `${expiresAt}.${base64Url(digest)}`;
}

export async function verifyStreamToken(input: {
  token: string;
  callSid: string;
  tenantId: string;
  businessId: string;
  secret: string;
  nowSeconds: number;
}): Promise<boolean> {
  const [expiresAtPart, signature, extra] = input.token.split(".");
  const expiresAt = Number(expiresAtPart);

  if (
    extra !== undefined ||
    !Number.isInteger(expiresAt) ||
    expiresAt < input.nowSeconds ||
    expiresAt > input.nowSeconds + 300 ||
    !signature
  ) {
    return false;
  }

  const expected = await issueStreamToken({
    ...input,
    nowSeconds: expiresAt - 300,
  });

  return constantTimeEqual(input.token, expected);
}

function base64Url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function constantTimeEqual(left: string, right: string): boolean {
  const a = new TextEncoder().encode(left);
  const b = new TextEncoder().encode(right);
  let difference = a.length ^ b.length;

  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    difference |= (a[i] ?? 0) ^ (b[i] ?? 0);
  }

  return difference === 0;
}
