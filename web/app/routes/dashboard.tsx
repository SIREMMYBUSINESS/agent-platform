import { Link, redirect, useNavigate, useRevalidator } from "react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Route } from "./+types/dashboard";
import { clearSessionCookie, readSessionApiKey } from "../.server/session";

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
  constructor(readonly path: string, readonly kind: "not_configured" | "connection" | "redirected" | "invalid_response", readonly detail = "") {
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
    if (error.status === 401) return `${path} rejected the API key (HTTP 401). Check that this provisioned key is valid, unexpired, and not revoked.`;
    if (error.status === 403) return `${path} denied tenant access (HTTP 403). Check that this key has an enabled grant for this tenant.`;
    return `${path} returned HTTP ${error.status}. Check the staging API deployment and its database connection.`;
  }
  if (error instanceof PlatformRequestError) {
    if (error.kind === "not_configured") return `The staging Worker has no API origin configured for ${path}. Redeploy it with the staging API URL.`;
    if (error.kind === "redirected") return `The staging API redirected ${path} to a different host (${error.detail}). Check the staging API origin.`;
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
      // Render can issue canonical redirects. Fetch strips Authorization on
      // cross-origin redirects; reject a final response from any other host.
      redirect: "follow",
    });
  } catch (error) {
    const detail = safeFetchErrorCode(error);
    console.warn("Dashboard API request failed", { path: endpoint, errorCode: detail });
    throw new PlatformRequestError(endpoint, "connection", detail);
  }
  if (response.url && new URL(response.url).origin !== new URL(API_BASE).origin) {
    throw new PlatformRequestError(endpoint, "redirected", new URL(response.url).host);
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
function formatRelativeDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";
  const seconds = (date.getTime() - Date.now()) / 1000;
  const absolute = Math.abs(seconds);
  const units: Array<[number, Intl.RelativeTimeFormatUnit]> = [
    [31_536_000, "year"], [2_592_000, "month"], [86_400, "day"],
    [3_600, "hour"], [60, "minute"],
  ];
  const [size, unit] = units.find(([size]) => absolute >= size) || [1, "second"];
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(Math.round(seconds / size), unit);
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
  if (!apiKey) return redirect("/login");
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
    if (error instanceof PlatformApiError && error.status === 401) {
      return redirect("/login?reason=expired");
    }
    return {
      configured: true,
      connected: false,
      leads: [] as Lead[],
      conversations: [] as Conversation[],
      error: error instanceof PlatformApiError || error instanceof PlatformRequestError
        ? accessFailure(error.path, error)
        : "Your API key could not access the workspace. Check its tenant grant and permissions.",
    };
  }
}

export async function action({ request }: Route.ActionArgs) {
  if (!originMatches(request)) return { error: "Request origin could not be verified." };
  const form = await request.formData();
  if (String(form.get("intent") || "") !== "logout") {
    return { error: "Invalid workspace action." };
  }
  return new Response(null, {
    status: 303,
    headers: {
      Location: "/login",
      "Set-Cookie": clearSessionCookie(),
      "Cache-Control": "private, no-store",
    },
  });
}

function readableStatus(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ");
}

function matchesQuery(values: Array<string | undefined>, query: string) {
  const normalized = query.trim().toLocaleLowerCase();
  return !normalized || values.some((value) => value?.toLocaleLowerCase().includes(normalized));
}

export default function DashboardRoute({ loaderData }: Route.ComponentProps) {
  const [query, setQuery] = useState("");
  const [conversationFilter, setConversationFilter] = useState("all");
  const [conversationSort, setConversationSort] = useState<"updated" | "status" | "channel">("updated");
  const searchRef = useRef<HTMLInputElement>(null);
  const revalidator = useRevalidator();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const inField = event.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);
      if (event.key === "/" && !inField && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); searchRef.current?.focus(); }
      if (event.key === "Escape" && document.activeElement === searchRef.current) { setQuery(""); searchRef.current?.blur(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const navigate = useNavigate();
  const filteredLeads = useMemo(() => loaderData.leads.filter((lead) =>
    matchesQuery([lead.id, lead.contact_id, lead.status], query)
  ), [loaderData.leads, query]);
  const filteredConversations = useMemo(() => loaderData.conversations
    .filter((conversation) => matchesQuery([conversation.id, conversation.contact_id, conversation.channel, conversation.status, conversation.workflow_state], query))
    .filter((conversation) => {
      const blocked = /blocked/i.test(conversation.workflow_state);
      const closed = /(closed|resolved|complete)/i.test(conversation.status);
      if (conversationFilter === "blocked") return blocked;
      if (conversationFilter === "open") return !blocked && !closed;
      if (conversationFilter === "closed") return closed;
      return true;
    })
    .sort((a,b) => conversationSort === "updated" ? new Date(b.updated_at).getTime()-new Date(a.updated_at).getTime()
      : conversationSort === "channel" ? a.channel.localeCompare(b.channel) : a.status.localeCompare(b.status)),
    [loaderData.conversations, query, conversationFilter, conversationSort]);
  const channels = new Set(loaderData.conversations.map((conversation) => conversation.channel)).size;
  const blockedCount = loaderData.conversations.filter((conversation) => /blocked/i.test(conversation.workflow_state)).length;
  const plural = (n: number, singular: string, pluralForm = singular + "s") => n === 1 ? singular : pluralForm;

  return (
    <main className="workspace-shell workspace-dashboard">
      <header className="workspace-heading dashboard-hero">
        <div>
          <p className="eyebrow">Tenant workspace · Overview</p>
          <h1>Good to have you back.</h1>
          <p className="workspace-description">A clear view of the latest leads and customer conversations available to this workspace.</p>
        </div>
        {loaderData.connected && <div className="workspace-actions">
          <div className="workspace-segmented-control" role="group" aria-label="Workspace shortcuts">
            <button className="secondary-button refresh-button" type="button" onClick={() => revalidator.revalidate()} disabled={revalidator.state !== "idle"}>
              {revalidator.state !== "idle" ? "Refreshing…" : "Refresh data"}
            </button>
            <Link className="secondary-button" to="/leads">Leads</Link>
            <Link className="secondary-button" to="/conversations">Conversations</Link>
          </div>
          <Link className="primary-button" to="/onboarding">Configure workspace</Link>
        </div>}
      </header>

      {!loaderData.connected ? (
        <section className="card workspace-credentials">
          <div className="section-heading">
            <span className="number">!</span>
            <div><h2>Workspace unavailable</h2><p>{loaderData.error || "Connect with your provisioned API key to load workspace data."}</p></div>
          </div>
          <Link className="primary-button" to="/login">Connect with API key</Link>
        </section>
      ) : (
        <>
          <section className="workspace-stats dashboard-metrics" aria-label="Workspace data summary">
            <article className="stat-card metric-leads"><span>Leads loaded</span><strong>{loaderData.leads.length}</strong><small>{loaderData.leads.length} of 25 records</small><div className="stat-progress" role="progressbar" aria-label="Lead limit" aria-valuenow={loaderData.leads.length} aria-valuemin={0} aria-valuemax={25}><span style={{width: `${Math.min(loaderData.leads.length / 25 * 100, 100)}%`}} /></div></article>
            <article className="stat-card metric-conversations"><span>Conversations loaded</span><strong>{loaderData.conversations.length}</strong><small>{loaderData.conversations.length} of 25 records</small><div className="stat-progress" role="progressbar" aria-label="Conversation limit" aria-valuenow={loaderData.conversations.length} aria-valuemin={0} aria-valuemax={25}><span style={{width: `${Math.min(loaderData.conversations.length / 25 * 100, 100)}%`}} /></div></article>
            <article className="stat-card metric-channels"><span>Channels represented</span><strong>{channels}</strong><small>From up to 25 recent conversations</small></article>
          </section>

          <section className="dashboard-toolbar" aria-label="Workspace data controls">
            <div>
              <p className="eyebrow">Activity overview</p>
              <h2>Recent activity</h2>
              <p>Search the latest records by ID, contact, status, or channel.</p>
            </div>
            <label className="workspace-search">
              <span className="sr-only">Search leads and conversations</span>
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search leads and conversations…  /" ref={searchRef} aria-keyshortcuts="/" />
              {query && <button type="button" onClick={() => setQuery("")} aria-label="Clear search">Clear</button>}
            </label>
          </section>

          <section className="card data-card dashboard-data-card">
            <div className="data-heading">
              <div><p className="eyebrow">Lead management</p><h2>Recent leads</h2><p className="data-subtitle">Latest leads assigned to this tenant.</p></div>
              <div className="data-heading-actions"><span className="data-count data-count-label" aria-label={`${filteredLeads.length} matching leads`}>{filteredLeads.length} leads</span><Link className="view-all-link" to="/leads">View all</Link></div>
            </div>
            {filteredLeads.length === 0 ? <div className="empty-state">
              <strong>{loaderData.leads.length === 0 ? "No leads yet" : "No matching leads"}</strong>
              <p>{loaderData.leads.length === 0 ? "New leads will appear here when they enter your workspace." : "Try a different search term or clear the search."}</p>
              {loaderData.leads.length === 0 && <div className="lead-setup-checklist"><strong>Get started</strong><ol><li className={loaderData.connected ? "complete" : ""}><span>{loaderData.connected ? "✓" : "1"}</span> Create workspace</li><li className={channels > 0 ? "complete" : ""}><span>{channels > 0 ? "✓" : "2"}</span> <Link to="/onboarding">Connect a channel</Link></li><li><span>3</span> Receive first lead</li></ol></div>}
            </div> : (
              <div className="table-scroll"><table><thead><tr><th scope="col">Lead</th><th scope="col">Status</th><th scope="col">Priority score</th><th scope="col">Created</th></tr></thead>
                <tbody>{filteredLeads.map((lead) => <tr key={lead.id}>
                  <td><strong>{shortId(lead.id)}</strong><small>Contact {shortId(lead.contact_id)}</small></td>
                  <td><span className="status-chip">{readableStatus(lead.status)}</span></td>
                  <td>{typeof lead.scores?.priority_score === "number" ? lead.scores.priority_score : "—"}</td>
                  <td>{formatDate(lead.created_at)}</td>
                </tr>)}</tbody>
              </table></div>
            )}
          </section>

          {blockedCount > 0 && <section className="attention-bar" aria-live="polite"><span aria-hidden="true">!</span><strong>{blockedCount} blocked {plural(blockedCount, "conversation")} need{blockedCount === 1 ? "s" : ""} review</strong><button type="button" onClick={() => setConversationFilter("blocked")}>Review blocked</button></section>}
          <section id="recent-conversations" className="card data-card dashboard-data-card">
            <div className="data-heading">
              <div><p className="eyebrow">Receptionist inbox</p><h2>Recent conversations</h2><p className="data-subtitle">Latest conversations across connected channels.</p></div>
              <div className="data-heading-actions"><span className="data-count data-count-label" aria-label={`${filteredConversations.length} matching conversations`}>{filteredConversations.length} {plural(filteredConversations.length, "conversation")}</span><Link className="view-all-link" to="/conversations">View all</Link></div>
            </div>
            {filteredConversations.length === 0 ? <div className="empty-state">
              <strong>{loaderData.conversations.length === 0 ? "No conversations yet" : "No matching conversations"}</strong>
              <p>{loaderData.conversations.length === 0 ? "Customer conversations will appear here as your receptionist handles them." : "Try a different search term or clear the search."}</p>
            </div> : (
              <div className="conversation-filter-bar" role="group" aria-label="Filter conversations">{(["all","open","blocked","closed"] as const).map((filter) => <button key={filter} type="button" className={conversationFilter === filter ? "filter-chip active" : "filter-chip"} aria-pressed={conversationFilter === filter} onClick={() => setConversationFilter(filter)}>{filter[0].toUpperCase()+filter.slice(1)}</button>)}<label className="sort-control">Sort <select value={conversationSort} onChange={e=>setConversationSort(e.target.value as typeof conversationSort)}><option value="updated">Last updated</option><option value="status">Status</option><option value="channel">Channel</option></select></label></div>
              <div className="table-scroll"><table><thead><tr><th scope="col">Conversation</th><th scope="col">Channel</th><th scope="col">Status</th><th scope="col">Last updated</th></tr></thead>
                <tbody>{filteredConversations.map((conversation) => {
                  const isBlocked = /blocked/i.test(conversation.workflow_state);
                  const conversationUrl = `/conversations?search=${encodeURIComponent(conversation.id)}`;
                  return <tr key={conversation.id} className="conversation-row" tabIndex={0} aria-label={`Open conversation ${shortId(conversation.id)}`} onClick={() => navigate(conversationUrl)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); navigate(conversationUrl); } }}>
                    <td><strong>{shortId(conversation.id)}</strong><small>Contact {shortId(conversation.contact_id)}</small></td>
                    <td><span className="channel-label">{readableStatus(conversation.channel)}</span></td>
                    <td>{isBlocked
                      ? <span className="status-chip status-chip-blocked" title="This conversation is blocked in its workflow and needs a team member to review it.">Blocked: needs attention</span>
                      : <span className="status-chip">{readableStatus(conversation.status)}</span>}</td>
                    <td><time dateTime={conversation.updated_at} title={formatDate(conversation.updated_at)}>{formatRelativeDate(conversation.updated_at)}</time></td>
                  </tr>;
                })}</tbody>
              </table></div>
            )}
          </section>
        </>
      )}
      <footer className="workspace-footer">Data is tenant-scoped by the platform API. This view displays up to 25 recent records per section; use the platform's dedicated management tools for complete history and workflow actions.</footer>
    </main>
  );
}
