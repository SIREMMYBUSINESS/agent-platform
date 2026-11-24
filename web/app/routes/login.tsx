import { Form, Link } from "react-router";
import { createSessionCookie } from "../.server/session";
import type { Route } from "./+types/login";

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");
const STAGING_BYPASS_UI = import.meta.env.VITE_STAGING_AUTH_BYPASS_ENABLED === "true";

function originMatches(request: Request) {
  const origin = request.headers.get("Origin");
  return !!origin && origin === new URL(request.url).origin;
}

export function meta() {
  return [
    { title: "Sign in | Agent Platform" },
    { name: "description", content: "Sign in securely to your Agent Platform workspace." },
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
  if (!API_BASE) return { error: "Workspace authentication is not configured yet." };
  const form = await request.formData();
  const email = String(form.get("email") || "").trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254) {
    return { error: "Enter a valid email address." };
  }
  const intent = String(form.get("intent") || "magic-link");
  if (intent === "staging-bypass") {
    const accessCode = String(form.get("accessCode") || "");
    const tenantId = String(form.get("tenantId") || "");
    const sessionSecret = context.cloudflare.env.SESSION_ENCRYPTION_KEY;
    if (!STAGING_BYPASS_UI || !accessCode || !tenantId || !sessionSecret) {
      return { error: "Staging access is not configured." };
    }
    try {
      const response = await fetch(API_BASE + "/auth/staging-owner-session", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Staging-Auth-Bypass": accessCode,
        },
        body: JSON.stringify({ email, tenant_id: tenantId }),
        redirect: "manual",
      });
      if (!response.ok) return { error: "Staging access could not be authorized." };
      const payload = await response.json() as { session_token: string; expires_at: string; tenant_id: string };
      const expiry = Date.parse(payload.expires_at);
      if (!/^ws_[A-Za-z0-9_-]{43}$/.test(payload.session_token)
        || payload.tenant_id !== tenantId || !Number.isFinite(expiry)) {
        return { error: "The staging workspace session could not be created." };
      }
      const headers = new Headers({
        "Cache-Control": "private, no-store, max-age=0",
        "Referrer-Policy": "no-referrer",
        "Location": "/dashboard",
      });
      headers.append("Set-Cookie", await createSessionCookie(payload.session_token, sessionSecret, expiry));
      return new Response(null, { status: 303, headers });
    } catch {
      return { error: "Staging access could not be completed. Please try again." };
    }
  }
  try {
    const response = await fetch(API_BASE + "/auth/magic-link", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
      redirect: "manual",
    });
    if (response.status !== 202) {
      return {
        error: response.status >= 500
          ? "Email sign-in is temporarily unavailable. Please try again shortly."
          : "The sign-in request could not be completed.",
      };
    }
    return { sent: true };
  } catch {
    return { error: "The sign-in service could not be reached. Please try again." };
  }
}

export default function LoginRoute({ actionData }: Route.ComponentProps) {
  return (
    <main className="workspace-shell">
      <section className="card workspace-credentials">
        <p className="eyebrow">Agent Platform</p>
        <h1>Sign in to your workspace</h1>
        <p className="workspace-description">
          Enter your invited workspace email. We will send you a one-time sign-in link.
        </p>
        {actionData?.sent ? (
          <div className="notice" role="status">
            Check your inbox. If an eligible workspace account exists for that email, a sign-in link has been sent.
          </div>
        ) : (
          <Form method="post" className="key-form">
            <label htmlFor="workspace-email">Work email</label>
            <div className="key-entry">
              <input
                id="workspace-email"
                name="email"
                type="email"
                autoComplete="email"
                maxLength={254}
                required
                placeholder="you@company.com"
              />
              <button className="primary-button" type="submit">Email me a sign-in link</button>
            </div>
            {actionData?.error && <p className="notice error-notice" role="alert">{actionData.error}</p>}
            <p className="helper">
              Access is available to users invited by a workspace administrator. Sign-in links expire and can only be used once.
            </p>
          </Form>
          {STAGING_BYPASS_UI && (
            <Form method="post" className="key-form">
              <input type="hidden" name="intent" value="staging-bypass" />
              <h2>Temporary staging owner access</h2>
              <label htmlFor="staging-tenant-id">Staging tenant ID</label>
              <input id="staging-tenant-id" name="tenantId" type="text" required autoComplete="off" />
              <label htmlFor="staging-access-code">Staging access code</label>
              <input id="staging-access-code" name="accessCode" type="password" required autoComplete="off" />
              <button className="primary-button" type="submit">Open staging workspace</button>
              <p className="helper">Temporary staging-only access. Do not use production credentials.</p>
            </Form>
          )}
        )}
        <p className="helper"><Link to="/">Back to Agent Platform</Link></p>
      </section>
    </main>
  );
}
