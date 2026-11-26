import { Form, Link, useNavigation } from "react-router";
import { createSessionCookie } from "../.server/session";
import type { Route } from "./+types/login";

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");

type ListResponse<T> = { items: T[]; offset: number; limit: number };

class PlatformApiError extends Error {
  constructor(readonly path: string, readonly status: number) {
    super("Platform API returned " + status + ".");
  }
}

class PlatformRequestError extends Error {
  constructor(readonly path: string) {
    super("Platform API request failed.");
  }
}

function originMatches(request: Request) {
  const origin = request.headers.get("Origin");
  return !!origin && origin === new URL(request.url).origin;
}

async function validateApiKey(apiKey: string) {
  if (!API_BASE) throw new PlatformRequestError("configuration");
  for (const path of ["/leads?offset=0&limit=1", "/conversations?offset=0&limit=1"]) {
    let response: Response;
    try {
      response = await fetch(API_BASE + path, {
        headers: { Authorization: "Bearer " + apiKey, Accept: "application/json" },
        redirect: "follow",
      });
    } catch {
      throw new PlatformRequestError(path.split("?")[0]);
    }
    if (response.url && new URL(response.url).origin !== new URL(API_BASE).origin) {
      throw new PlatformRequestError(path.split("?")[0]);
    }
    if (!response.ok) throw new PlatformApiError(path.split("?")[0], response.status);
    try {
      const payload = await response.json() as ListResponse<unknown>;
      if (!payload || !Array.isArray(payload.items)) throw new Error("Invalid API response.");
    } catch {
      throw new PlatformRequestError(path.split("?")[0]);
    }
  }
}

function loginError(error: unknown) {
  if (error instanceof PlatformApiError) {
    if (error.status === 401) return "The API rejected this key. Check that it is the correct, unexpired staging key.";
    if (error.status === 403) return "This API key does not have the required tenant access or permissions.";
    return "The staging API returned HTTP " + error.status + ". Check the API service and try again.";
  }
  if (error instanceof PlatformRequestError) {
    return "The staging API could not be reached or returned an unexpected response. Check the API URL and service availability.";
  }
  return "The workspace session could not be created. Check the staging Worker session configuration.";
}

export function meta() {
  return [
    { title: "Workspace login | Agent Platform" },
    { name: "description", content: "Connect securely to your Agent Platform workspace." },
    { name: "robots", content: "noindex, nofollow" },
  ];
}

export function headers() {
  return {
    "Cache-Control": "private, no-store, max-age=0",
    "Referrer-Policy": "no-referrer",
  };
}

export async function action({ request, context }: Route.ActionArgs) {
  if (!originMatches(request)) return { error: "Request origin could not be verified." };
  if (!API_BASE) return { error: "The staging API URL is not configured." };
  const form = await request.formData();
  const apiKey = String(form.get("apiKey") || "").trim();
  if (!/^ap_[A-Za-z0-9_-]{43}$/.test(apiKey)) {
    return { error: "Enter a valid provisioned API key." };
  }
  const sessionSecret = context.cloudflare.env.SESSION_ENCRYPTION_KEY;
  if (!sessionSecret) return { error: "Workspace session encryption is not configured." };
  try {
    await validateApiKey(apiKey);
    const headers = new Headers({
      "Cache-Control": "private, no-store, max-age=0",
      "Referrer-Policy": "no-referrer",
      "Location": "/dashboard",
    });
    headers.append("Set-Cookie", await createSessionCookie(apiKey, sessionSecret));
    return new Response(null, { status: 303, headers });
  } catch (error) {
    return { error: loginError(error) };
  }
}

export default function LoginRoute({ actionData }: Route.ComponentProps) {
  const navigation = useNavigation();
  const isSubmitting = navigation.state !== "idle";
  return (
    <main className="workspace-shell">
      <section className="card workspace-credentials" aria-busy={isSubmitting}>
        <p className="eyebrow">Agent Platform · Staging</p>
        <h1>Connect to your workspace</h1>
        <p className="workspace-description">
          Enter a provisioned staging API key to securely connect to its assigned tenant.
        </p>
        <Form method="post" className="key-form">
          <label htmlFor="workspace-api-key">Provisioned API key</label>
          <div className="key-entry">
            <input
              id="workspace-api-key"
              name="apiKey"
              type="password"
              autoComplete="off"
              spellCheck={false}
              required
              placeholder="ap_…"
              disabled={isSubmitting}
            />
            <button className="primary-button" type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Verifying key…" : "Connect"}
            </button>
          </div>
          {actionData?.error && <p className="notice error-notice" role="alert">{actionData.error}</p>}
          <p className="helper">
            The key is validated server-side and stored only in an encrypted, one-hour HttpOnly session. It is never placed in browser storage or returned to client-side code. Use a provisioned key with the required lead and conversation permissions.
          </p>
        </Form>
        <p className="helper"><Link to="/">Back to Agent Platform</Link></p>
      </section>
    </main>
  );
}
