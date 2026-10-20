import { Form } from "react-router";
import type { Route } from "./+types/dashboard";
import { createSessionCookie, readSessionApiKey } from "../.server/session";

type Lead = {
  id: string;
  contact_id: string;
  status: string;
  created_at: string;
  scores?: { priority_score?: number | null } | null;
};
type Conversation = {
  id: string;
  contact_id: string;
  channel: string;
  status: string;
  workflow_state: string;
  updated_at: string;
};
type ListResponse<T> = { items: T[]; offset: number; limit: number };

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");

class PlatformApiError extends Error {
  constructor(readonly path: string, readonly status: number) {
    super("Platform API returned " + status + ".");
  }
}

class PlatformRequestError extends Error {
  constructor(readonly path: string, readonly kind: "not_configured" | "connection" | "invalid_response", readonly detail = "") {
    super(kind);
  }
}

function safeFetchErrorCode(error: unknown) {
  if (error && typeof error === "object") {
    const cause = "cause" in error ? error.cause : undefined;
    if (cause && typeof cause === "object" && "code" in cause && typeof cause.code === "string" && /^[A-Za-z0-9_-]{1,40}$/.test(cause.code)) {
      return cause.code;
    }
    if ("name" in error && typeof error.name === "string" && /^[A-Za-z]+$/.test(error.name)) {
      return error.name;
    }
  }
  return "unknown";
}

function accessFailure(path: string, error: unknown) {
  if (error instanceof PlatformApiError) {
    if (error.status === 401) return `${path} rejected the API key (HTTP 401). Check that this exact key was inserted in the staging database, and that it is unexpired and not revoked.`;
    if (error.status === 403) return `${path} denied tenant access (HTTP 403). Check the enabled, unexpired grant for this key in the database used by the staging API.`;
    return `${path} returned HTTP ${error.status}. Check the staging API deployment and its database connection.`;
  }
  if (error instanceof PlatformRequestError) {
    if (error.kind === "not_configured") return `The staging Worker has no API origin configured for ${path}. Redeploy it with the staging API URL.`;
    if (error.kind === "invalid_response") return `The staging API returned an invalid response for ${path}. Check the Render service logs.`;
    return `The staging Worker could not complete a connection to ${API_BASE ? new URL(API_BASE).host : "the API"} for ${path} (network error: ${error.detail || "unknown"}). Check the Render service logs and retry while the service is awake.`;
  }
  return `Could not reach ${path}. Check the staging API URL and availability.`;
}

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Workspace | Agent Platform" },
    { name: "description", content: "Review tenant-scoped leads and receptionist conversations." },
    { name: "robots", content: "noindex, nofollow" },
  ];
}

export function headers() {
  return { "Cache-Control": "private, no-store, max-age=0" };
}

async function fetchPlatform<T>(path: string, apiKey: string): Promise<T> {
  const endpoint = path.split("?")[0];
  if (!API_BASE) throw new PlatformRequestError(endpoint, "not_configured");
  let response: Response;
  try {
    response = await fetch(API_BASE + path, {
      headers: { Authorization: "Bearer " + apiKey, Accept: "application/json" },
      redirect: "error",
    });
  } catch (error) {
    const detail = safeFetchErrorCode(error);
    console.warn("Dashboard API request failed", { path: endpoint, errorCode: detail });
    throw new PlatformRequestError(endpoint, "connection", detail);
  }
  if (!response.ok) throw new PlatformApiError(path.split("?")[0], response.status);
  try {
    return await response.json() as T;
  } catch {
    throw new PlatformRequestError(endpoint, "invalid_response");
  }
}

function shortId(value: string) {
  return value.length > 15 ? value.slice(0, 8) + "…" + value.slice(-4) : value;
}
function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Date unavailable"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function originMatches(request: Request) {
  const origin = request.headers.get("Origin");
  return !!origin && origin === new URL(request.url).origin;
}

export async function loader({ request, context }: Route.LoaderArgs) {
  const sessionSecret = context.cloudflare.env.SESSION_ENCRYPTION_KEY;
  if (!API_BASE || !sessionSecret) {
    return {
      configured: false,
      connected: false,
      leads: [] as Lead[],
      conversations: [] as Conversation[],
      error: "Workspace authentication is not configured yet.",
    };
  }
  const apiKey = await readSessionApiKey(request, sessionSecret);
  if (!apiKey) {
    return {
      configured: true,
      connected: false,
      leads: [] as Lead[],
      conversations: [] as Conversation[],
      error: null as string | null,
    };
  }
  try {
    const [leadResult, conversationResult] = await Promise.all([
      fetchPlatform<ListResponse<Lead>>("/leads?offset=0&limit=25", apiKey),
      fetchPlatform<ListResponse<Conversation>>("/conversations?offset=0&limit=25", apiKey),
    ]);
    return {
      configured: true,
      connected: true,
      leads: leadResult.items,
      conversations: conversationResult.items,
      error: null as string | null,
    };
  } catch (error) {
    return {
      configured: true,
      connected: false,
      leads: [] as Lead[],
      conversations: [] as Conversation[],
      error: error instanceof PlatformApiError || error instanceof PlatformRequestError
        ? accessFailure(error.path, error)
        : "Your session could not access the workspace. Sign in again.",
    };
  }
}

export async function action({ request, context }: Route.ActionArgs) {
  if (!originMatches(request)) return { error: "Request origin could not be verified." };
  const form = await request.formData();
  const intent = String(form.get("intent") || "");

  if (intent === "logout") {
    return new Response(null, {
      status: 303,
      headers: {
        Location: "/dashboard",
        "Set-Cookie": "__Host-agent_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0",
        "Cache-Control": "private, no-store",
      },
    });
  }

  const apiKey = String(form.get("apiKey") || "").trim();
  if (!/^ap_[A-Za-z0-9_-]{43}$/.test(apiKey)) {
    return { error: "Enter a valid provisioned platform API key." };
  }
  const sessionSecret = context.cloudflare.env.SESSION_ENCRYPTION_KEY;
  if (!sessionSecret) return { error: "Workspace authentication is not configured yet." };

  const checks = await Promise.allSettled([
      fetchPlatform<ListResponse<Lead>>("/leads?offset=0&limit=1", apiKey),
      fetchPlatform<ListResponse<Conversation>>("/conversations?offset=0&limit=1", apiKey),
  ]);
  const failures = checks.flatMap((check) => check.status === "rejected" ? [check.reason] : []);
  if (failures.length) {
    return {
      error: [...new Set(failures.map((failure) => failure instanceof PlatformApiError || failure instanceof PlatformRequestError
        ? accessFailure(failure.path, failure)
        : "Could not reach the staging API. Check its configured URL and availability."))].join(" "),
    };
  }
  try {
    return new Response(null, {
      status: 303,
      headers: {
        Location: "/dashboard",
        "Set-Cookie": await createSessionCookie(apiKey, sessionSecret),
        "Cache-Control": "private, no-store",
      },
    });
  } catch {
    return { error: "Workspace session could not be created. Check the Worker session encryption configuration." };
  }
}

export default function DashboardRoute({ loaderData, actionData }: Route.ComponentProps) {
  return (
    <main className="workspace-shell">
      <header className="workspace-heading">
        <div>
          <p className="eyebrow">Tenant workspace</p>
          <h1>Your receptionist activity</h1>
          <p className="workspace-description">Review recent leads and conversations. The API scopes each result to the tenant assigned to your key.</p>
        </div>
        {loaderData.connected && <Form method="post"><button className="secondary-button" type="submit" name="intent" value="logout">Sign out</button></Form>}
      </header>

      {!loaderData.connected ? (
        <section className="card workspace-credentials">
          <div className="section-heading">
            <span className="number">1</span>
            <div><h2>Connect to your workspace</h2><p>Your key is sent to the Worker and kept in an encrypted, one-hour HttpOnly session.</p></div>
          </div>
          {(actionData?.error || loaderData.error) && <p className="notice error-notice" role="alert">{actionData?.error || loaderData.error}</p>}
          <Form method="post" className="key-form">
            <input type="hidden" name="intent" value="login" />
            <label htmlFor="workspace-api-key">Provisioned API key</label>
            <div className="key-entry">
              <input id="workspace-api-key" name="apiKey" type="password" autoComplete="off" spellCheck={false} required placeholder="ap_…" />
              <button className="primary-button" type="submit">Connect</button>
            </div>
            <p className="helper">The browser never receives the key from the server. The key stays out of browser storage and is removed when the session expires or you sign out. Use a provisioned key with lead_reader and conversation_reader access.</p>
          </Form>
        </section>
      ) : (
        <>
          <section className="workspace-stats" aria-label="Workspace summary">
            <article className="stat-card"><span>Recent leads</span><strong>{loaderData.leads.length}</strong><small>Up to 25 most recent</small></article>
            <article className="stat-card"><span>Conversations</span><strong>{loaderData.conversations.length}</strong><small>Up to 25 most recent</small></article>
            <article className="stat-card"><span>Connection</span><strong className="connection-ready">Connected</strong><small>Railway API data loaded</small></article>
          </section>

          <section className="card data-card">
            <div className="data-heading"><div><p className="eyebrow">Lead management</p><h2>Recent leads</h2></div><span className="data-count">{loaderData.leads.length}</span></div>
            {loaderData.leads.length === 0 ? <p className="empty-state">No leads are available for this tenant yet.</p> : (
              <div className="table-scroll"><table><thead><tr><th>Lead</th><th>Status</th><th>Priority</th><th>Created</th></tr></thead>
                <tbody>{loaderData.leads.map((lead) => <tr key={lead.id}>
                  <td><strong>{shortId(lead.id)}</strong><small>Contact {shortId(lead.contact_id)}</small></td>
                  <td><span className="status-chip">{lead.status.replaceAll("_", " ")}</span></td>
                  <td>{typeof lead.scores?.priority_score === "number" ? lead.scores.priority_score : "—"}</td>
                  <td>{formatDate(lead.created_at)}</td>
                </tr>)}</tbody>
              </table></div>
            )}
          </section>

          <section className="card data-card">
            <div className="data-heading"><div><p className="eyebrow">Receptionist inbox</p><h2>Recent conversations</h2></div><span className="data-count">{loaderData.conversations.length}</span></div>
            {loaderData.conversations.length === 0 ? <p className="empty-state">No conversations are available for this tenant yet.</p> : (
              <div className="table-scroll"><table><thead><tr><th>Conversation</th><th>Channel</th><th>Status</th><th>Updated</th></tr></thead>
                <tbody>{loaderData.conversations.map((conversation) => <tr key={conversation.id}>
                  <td><strong>{shortId(conversation.id)}</strong><small>Contact {shortId(conversation.contact_id)}</small></td>
                  <td>{conversation.channel}</td>
                  <td><span className="status-chip">{conversation.status.replaceAll("_", " ")}</span><small>{conversation.workflow_state.replaceAll("_", " ")}</small></td>
                  <td>{formatDate(conversation.updated_at)}</td>
                </tr>)}</tbody>
              </table></div>
            )}
          </section>
        </>
      )}
      <footer className="workspace-footer">For setup and provider checks, use onboarding. Outreach actions remain subject to provider readiness and tenant policy.</footer>
    </main>
  );
}
