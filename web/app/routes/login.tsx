import { useState } from "react";
import { Form, Link, useNavigation } from "react-router";
import { createSessionCookie } from "../.server/session";
import type { Route } from "./+types/login";

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");
const API_KEY_PATTERN = /^ap_[A-Za-z0-9_-]{43}$/;

type WorkspaceIdentity = {
  principal_id: string;
  tenant_id: string;
  roles: string[];
  auth_source: string;
};

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

  // Authentication must not depend on feature-specific permissions or data
  // availability. Individual workspace loaders enforce their own capabilities.
  const path = "/auth/me";
  let response: Response;
  try {
    response = await fetch(API_BASE + path, {
      headers: { Authorization: "Bearer " + apiKey, Accept: "application/json" },
      redirect: "follow",
    });
  } catch {
    throw new PlatformRequestError(path);
  }

  if (response.url && new URL(response.url).origin !== new URL(API_BASE).origin) {
    throw new PlatformRequestError(path);
  }
  if (!response.ok) throw new PlatformApiError(path, response.status);

  let payload: WorkspaceIdentity;
  try {
    payload = await response.json() as WorkspaceIdentity;
  } catch {
    throw new PlatformRequestError(path);
  }

  if (
    !payload ||
    typeof payload !== "object" ||
    typeof payload.principal_id !== "string" || !payload.principal_id.trim() ||
    typeof payload.tenant_id !== "string" || !payload.tenant_id.trim() ||
    typeof payload.auth_source !== "string" || !payload.auth_source.trim() ||
    !Array.isArray(payload.roles) ||
    !payload.roles.every((role) => typeof role === "string")
  ) {
    throw new PlatformRequestError(path);
  }
}
function loginError(error: unknown) {
  if (error instanceof PlatformApiError) {
    if (error.status === 401) return error.path + " rejected this key. Check that it is the correct, unexpired staging key.";
    if (error.status === 403) return error.path + " denied tenant access. Check the key's tenant grant.";
    if (error.status === 429) return error.path + " is temporarily rate-limited. Wait briefly before trying again; this response does not mean the key is invalid.";
    if (error.status >= 500) return error.path + " is temporarily unavailable. Check the API service and try again.";
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
          <p className="helper" id="api-key-help">The key is validated server-side against your authenticated workspace identity and stored only in an encrypted, one-hour HttpOnly session. It is never placed in browser storage or returned to client-side code. Access to leads, conversations, and other workspace features is checked separately when you open those pages.</p>
        </Form>
        <p className="helper"><Link to="/">Back to Agent Platform</Link></p>
      </section>
    </main>
  );
}
