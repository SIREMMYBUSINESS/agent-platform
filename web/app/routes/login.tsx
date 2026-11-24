import { Form, Link } from "react-router";
import type { Route } from "./+types/login";

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");

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

export async function action({ request }: Route.ActionArgs) {
  if (!originMatches(request)) return { error: "Request origin could not be verified." };
  if (!API_BASE) return { error: "Workspace authentication is not configured yet." };
  const form = await request.formData();
  const email = String(form.get("email") || "").trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254) {
    return { error: "Enter a valid email address." };
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
        )}
        <p className="helper"><Link to="/">Back to Agent Platform</Link></p>
      </section>
    </main>
  );
}
