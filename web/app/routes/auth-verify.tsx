import { Form, Link } from "react-router";
import type { Route } from "./+types/auth-verify";
import {
  clearPendingLoginCookie,
  createPendingLoginCookie,
  createSessionCookie,
  readPendingLoginTicket,
} from "../.server/session";

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");

type MembershipChoice = {
  membership_id: string;
  tenant_id: string;
  workspace_label: string;
  role: string;
};

function originMatches(request: Request) {
  const origin = request.headers.get("Origin");
  return !!origin && origin === new URL(request.url).origin;
}

function responseHeaders() {
  return {
    "Cache-Control": "private, no-store, max-age=0",
    "Referrer-Policy": "no-referrer",
  };
}

export function meta() {
  return [
    { title: "Verify sign-in | Agent Platform" },
    { name: "robots", content: "noindex, nofollow" },
  ];
}

export function headers() {
  return responseHeaders();
}

export async function loader({ request }: Route.LoaderArgs) {
  const token = new URL(request.url).searchParams.get("token") || "";
  return { hasToken: /^ml_[A-Za-z0-9_-]{43}$/.test(token), token };
}

export async function action({ request, context }: Route.ActionArgs) {
  if (!originMatches(request)) return { error: "Request origin could not be verified." };
  if (!API_BASE) return { error: "Workspace authentication is not configured yet." };
  const sessionSecret = context.cloudflare.env.SESSION_ENCRYPTION_KEY;
  if (!sessionSecret) return { error: "Workspace authentication is not configured yet." };
  const form = await request.formData();
  const intent = String(form.get("intent") || "");

  if (intent === "verify") {
    const token = String(form.get("token") || "");
    if (!/^ml_[A-Za-z0-9_-]{43}$/.test(token)) {
      return { error: "This sign-in link is invalid or expired. Request a new link." };
    }
    try {
      const response = await fetch(API_BASE + "/auth/magic-link/verify", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
        redirect: "manual",
      });
      if (!response.ok) return { error: "This sign-in link is invalid or expired. Request a new link." };
      const payload = await response.json() as {
        login_ticket: string;
        display_name: string;
        memberships: MembershipChoice[];
      };
      if (!/^lt_[A-Za-z0-9_-]{43}$/.test(payload.login_ticket)
        || !Array.isArray(payload.memberships) || payload.memberships.length === 0) {
        return { error: "No active workspace is available for this account." };
      }
      const cookie = await createPendingLoginCookie(payload.login_ticket, sessionSecret);
      return new Response(JSON.stringify({
        verified: true,
        displayName: payload.display_name,
        memberships: payload.memberships,
      }), {
        status: 200,
        headers: {
          ...responseHeaders(),
          "Content-Type": "application/json",
          "Set-Cookie": cookie,
        },
      });
    } catch {
      return { error: "The sign-in service could not be reached. Request a new link and try again." };
    }
  }

  if (intent === "choose-workspace") {
    const loginTicket = await readPendingLoginTicket(request, sessionSecret);
    const membershipId = String(form.get("membershipId") || "");
    if (!loginTicket || !/^[0-9a-f-]{36}$/i.test(membershipId)) {
      return { error: "Workspace selection expired. Request a new sign-in link." };
    }
    try {
      const response = await fetch(API_BASE + "/auth/session", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ login_ticket: loginTicket, membership_id: membershipId }),
        redirect: "manual",
      });
      if (!response.ok) return { error: "This workspace is no longer available. Request a new sign-in link." };
      const payload = await response.json() as { session_token: string; expires_at: string };
      const expiry = Date.parse(payload.expires_at);
      if (!/^ws_[A-Za-z0-9_-]{43}$/.test(payload.session_token) || !Number.isFinite(expiry)) {
        return { error: "The workspace session could not be created." };
      }
      const headers = new Headers(responseHeaders());
      headers.append("Set-Cookie", await createSessionCookie(payload.session_token, sessionSecret, expiry));
      headers.append("Set-Cookie", clearPendingLoginCookie());
      headers.set("Location", "/dashboard");
      return new Response(null, { status: 303, headers });
    } catch {
      return { error: "The workspace session could not be created. Please try again." };
    }
  }

  return { error: "Invalid sign-in action." };
}

export default function VerifyRoute({ loaderData, actionData }: Route.ComponentProps) {
  const memberships = actionData && "memberships" in actionData
    ? actionData.memberships as MembershipChoice[]
    : [];
  return (
    <main className="workspace-shell">
      <section className="card workspace-credentials">
        <p className="eyebrow">Agent Platform</p>
        {memberships.length > 0 ? (
          <>
            <h1>Choose your workspace</h1>
            <p className="workspace-description">
              {actionData && "displayName" in actionData && actionData.displayName
                ? "Welcome, " + actionData.displayName + ". Select the workspace you want to open."
                : "Select the workspace you want to open."}
            </p>
            <div className="stack">
              {memberships.map((membership) => (
                <Form method="post" key={membership.membership_id} className="card">
                  <input type="hidden" name="intent" value="choose-workspace" />
                  <input type="hidden" name="membershipId" value={membership.membership_id} />
                  <div className="data-heading">
                    <div>
                      <h2>{membership.workspace_label || "Workspace"}</h2>
                      <p className="helper">{membership.role} · {membership.tenant_id}</p>
                    </div>
                    <button className="primary-button" type="submit">Open workspace</button>
                  </div>
                </Form>
              ))}
            </div>
          </>
        ) : (
          <>
            <h1>Confirm your sign-in</h1>
            <p className="workspace-description">
              This page verifies the one-time link only after you press the button. Opening the email does not consume it.
            </p>
            {loaderData.hasToken ? (
              <Form method="post">
                <input type="hidden" name="intent" value="verify" />
                <input type="hidden" name="token" value={loaderData.token} />
                <button className="primary-button" type="submit">Verify email and continue</button>
              </Form>
            ) : (
              <p className="notice error-notice" role="alert">This sign-in link is missing or invalid.</p>
            )}
          </>
        )}
        {actionData && "error" in actionData && actionData.error
          && <p className="notice error-notice" role="alert">{actionData.error}</p>}
        <p className="helper"><Link to="/login">Request another sign-in link</Link></p>
      </section>
    </main>
  );
}
