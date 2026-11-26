import { useState } from "react";
import { Form, Link, useNavigation } from "react-router";
import { createSessionCookie } from "../.server/session";
import type { Route } from "./+types/login";

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");
const API_KEY_PATTERN = /^ap_[A-Za-z0-9_-]{43}$/;

type ListResponse<T> = { items: T[]; offset: number; limit: number };

class PlatformApiError extends Error {
  constructor(readonly path: string, readonly status: number) { super("Platform API returned " + status + "."); }
}
class PlatformRequestError extends Error {
  constructor(readonly path: string) { super("Platform API request failed."); }
}
function originMatches(request: Request) {
  const origin = request.headers.get("Origin");
  return !!origin && origin === new URL(request.url).origin;
}
function safeReturnTo(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/dashboard";
  try {
    const destination = new URL(value, "https://agent-platform.invalid");
    if (destination.origin !== "https://agent-platform.invalid" || !["/dashboard", "/onboarding", "/leads", "/conversations"].includes(destination.pathname)) return "/dashboard";
    return destination.pathname + destination.search + destination.hash;
  } catch { return "/dashboard"; }
}
export async function loader({ request }: Route.LoaderArgs) {
  const url = new URL(request.url);
  return { returnTo: safeReturnTo(url.searchParams.get("returnTo")) };
}
async function validateApiKey(apiKey: string) {
  if (!API_BASE) throw new PlatformRequestError("configuration");
  for (const path of ["/leads?offset=0&limit=1", "/conversations?offset=0&limit=1"]) {
    let response: Response;
    try {
      response = await fetch(API_BASE + path, { headers: { Authorization: "Bearer " + apiKey, Accept: "application/json" }, redirect: "follow" });
    } catch { throw new PlatformRequestError(path.split("?")[0]); }
    if (response.url && new URL(response.url).origin !== new URL(API_BASE).origin) throw new PlatformRequestError(path.split("?")[0]);
    if (!response.ok) throw new PlatformApiError(path.split("?")[0], response.status);
    try {
      const payload = await response.json() as ListResponse<unknown>;
      if (!payload || !Array.isArray(payload.items)) throw new Error("Invalid API response.");
    } catch { throw new PlatformRequestError(path.split("?")[0]); }
  }
}
function loginError(error: unknown) {
  if (error instanceof PlatformApiError) {
    if (error.status === 401) return error.path + " rejected this key. Check that it is the correct, unexpired staging key.";
    if (error.status === 403) return error.path + " denied access. Check the key's tenant grant and permissions.";
    return error.path + " returned HTTP " + error.status + ". Check the API service and try again.";
  }
  if (error instanceof PlatformRequestError) return "The staging API request for " + error.path + " could not be completed. Check the API URL and service availability.";
  return "The workspace session could not be created. Check the staging Worker session configuration.";
}
export function meta() {
  return [
    { title: "Staging workspace sign in | Agent Platform" },
    { name: "description", content: "Sign in securely to your Agent Platform staging workspace with a provisioned API key." },
    { name: "robots", content: "noindex, nofollow" },
  ];
}
export function headers() {
  return { "Cache-Control": "private, no-store, max-age=0", "Referrer-Policy": "no-referrer" };
}
export async function action({ request, context }: Route.ActionArgs) {
  if (!originMatches(request)) return { error: "Request origin could not be verified." };
  if (!API_BASE) return { error: "The staging API URL is not configured." };
  const form = await request.formData();
  const apiKey = String(form.get("apiKey") || "").trim();
  const returnTo = safeReturnTo(form.get("returnTo"));
  if (!API_KEY_PATTERN.test(apiKey)) return { error: "Enter a valid provisioned API key." };
  const sessionSecret = context.cloudflare.env.SESSION_ENCRYPTION_KEY;
  if (!sessionSecret) return { error: "Workspace session encryption is not configured." };
  try {
    await validateApiKey(apiKey);
    const headers = new Headers({ "Cache-Control": "private, no-store, max-age=0", "Referrer-Policy": "no-referrer", Location: returnTo });
    headers.append("Set-Cookie", await createSessionCookie(apiKey, sessionSecret));
    return new Response(null, { status: 303, headers });
  } catch (error) { return { error: loginError(error) }; }
}
export default function LoginRoute({ actionData, loaderData }: Route.ComponentProps) {
  const navigation = useNavigation();
  const [apiKey, setApiKey] = useState("");
  const isSubmitting = navigation.state !== "idle";
  const isValid = API_KEY_PATTERN.test(apiKey.trim());
  return (
    <main className="workspace-shell">
      <section className="card workspace-credentials" aria-busy={isSubmitting}>
        <p className="eyebrow">Agent Platform · Staging</p>
        <h1>Connect to your workspace</h1>
        <p className="workspace-description">Enter a provisioned staging API key to securely connect to its assigned tenant.</p>
        <Form method="post" className="key-form">
          <input type="hidden" name="returnTo" value={loaderData.returnTo} />
          <label htmlFor="workspace-api-key">Provisioned API key</label>
          <div className="key-entry">
            <input id="workspace-api-key" name="apiKey" type="password" autoComplete="off" spellCheck={false} required maxLength={46} placeholder="ap_…" value={apiKey} onChange={(event) => setApiKey(event.target.value)} disabled={isSubmitting} aria-describedby="api-key-help" />
            <button className="primary-button" type="submit" disabled={isSubmitting || !isValid}>
              {isSubmitting ? "Signing in…" : "Sign in to staging workspace"}
            </button>
          </div>
          {actionData?.error && <p className="notice error-notice" role="alert">{actionData.error}</p>}
          <p className="helper" id="api-key-help">The key is validated server-side and stored only in an encrypted, one-hour HttpOnly session. It is never placed in browser storage or returned to client-side code. Use a provisioned key with the required lead and conversation permissions.</p>
        </Form>
        <p className="helper"><Link to="/">Back to Agent Platform</Link></p>
      </section>
    </main>
  );
}
