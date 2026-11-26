import { useMemo, useState } from "react";
import { Link, redirect } from "react-router";
import { readSessionApiKey } from "../.server/session";
import type { Route } from "./+types/conversations";

type Conversation = {
  id: string;
  contact_id: string;
  channel: string;
  status: string;
  workflow_state: string;
  started_at: string;
  updated_at: string;
};
type ListResponse<T> = { items: T[]; offset: number; limit: number };
const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");
const PAGE_SIZE = 25;

export function meta() {
  return [
    { title: "Conversations | Staging Workspace | Agent Platform" },
    { name: "description", content: "Review and filter recent tenant-scoped customer conversations in the Agent Platform staging inbox." },
    { name: "robots", content: "noindex, nofollow" },
  ];
}
export function headers() { return { "Cache-Control": "private, no-store, max-age=0" }; }
export async function loader({ request, context }: Route.LoaderArgs) {
  const secret = context.cloudflare.env.SESSION_ENCRYPTION_KEY;
  const apiKey = secret ? await readSessionApiKey(request, secret) : null;
  if (!apiKey) return redirect("/login?returnTo=%2Fconversations");
  const url = new URL(request.url);
  const rawOffset = Number(url.searchParams.get("offset") || 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  if (!API_BASE) return { items: [] as Conversation[], offset, limit: PAGE_SIZE, hasNext: false, error: "The staging API URL is not configured." };
  try {
    const response = await fetch(`${API_BASE}/conversations?offset=${offset}&limit=${PAGE_SIZE}`, {
      headers: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }, redirect: "follow",
    });
    if (response.url && new URL(response.url).origin !== new URL(API_BASE).origin) return { items: [] as Conversation[], offset, limit: PAGE_SIZE, hasNext: false, error: "The staging API redirected to an unexpected host." };
    if (response.status === 401) return redirect("/login?reason=expired&returnTo=%2Fconversations");
    if (!response.ok) return { items: [] as Conversation[], offset, limit: PAGE_SIZE, hasNext: false, error: response.status === 403 ? "This API key does not have permission to read conversations for this tenant." : `The conversations service returned HTTP ${response.status}.` };
    const payload = await response.json() as ListResponse<Conversation>;
    if (!payload || !Array.isArray(payload.items)) throw new Error("Invalid response");
    return { items: payload.items, offset, limit: PAGE_SIZE, hasNext: payload.items.length === PAGE_SIZE, error: null as string | null };
  } catch {
    return { items: [] as Conversation[], offset, limit: PAGE_SIZE, hasNext: false, error: "Could not load conversations. Check staging API availability and retry." };
  }
}
function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function shortId(value: string) { return value.length > 16 ? value.slice(0, 8) + "…" + value.slice(-4) : value; }
function readable(value: string) { return value.replaceAll("_", " ").replaceAll("-", " "); }

export default function ConversationsRoute({ loaderData }: Route.ComponentProps) {
  const [query, setQuery] = useState("");
  const [channel, setChannel] = useState("all");
  const [status, setStatus] = useState("all");
  const channels = useMemo(() => [...new Set(loaderData.items.map((item) => item.channel))].sort(), [loaderData.items]);
  const statuses = useMemo(() => [...new Set(loaderData.items.map((item) => item.status))].sort(), [loaderData.items]);
  const filtered = useMemo(() => loaderData.items.filter((item) => {
    const search = query.trim().toLocaleLowerCase();
    return (!search || [item.id, item.contact_id, item.channel, item.status, item.workflow_state].some((v) => v.toLocaleLowerCase().includes(search)))
      && (channel === "all" || item.channel === channel)
      && (status === "all" || item.status === status);
  }), [loaderData.items, query, channel, status]);
  const previous = Math.max(0, loaderData.offset - loaderData.limit);
  const next = loaderData.offset + loaderData.limit;
  return (
    <main className="workspace-shell workspace-list-page">
      <header className="workspace-heading"><div><p className="eyebrow">Tenant workspace · Inbox</p><h1>Conversations</h1><p className="workspace-description">Review recent customer conversations, channels, statuses, and workflow states.</p></div><div className="workspace-actions"><Link className="secondary-button" to="/dashboard">Overview</Link><Link className="secondary-button" to="/leads">Leads</Link></div></header>
      {loaderData.error ? <section className="card workspace-credentials" role="alert"><h2>Inbox unavailable</h2><p>{loaderData.error}</p><Link className="primary-button" to="/dashboard">Return to workspace</Link></section> : <>
        <section className="card list-toolbar" aria-label="Conversation filters">
          <label className="workspace-search"><span className="sr-only">Search this page of conversations</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search ID, contact, workflow…" /></label>
          <label className="filter-control">Channel<select value={channel} onChange={(event) => setChannel(event.target.value)}><option value="all">All channels</option>{channels.map((value) => <option key={value} value={value}>{readable(value)}</option>)}</select></label>
          <label className="filter-control">Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All statuses</option>{statuses.map((value) => <option key={value} value={value}>{readable(value)}</option>)}</select></label>
          <span className="data-count">{filtered.length} shown</span>
        </section>
        <section className="card data-card"><div className="data-heading"><div><p className="eyebrow">Customer inbox</p><h2>Recent conversations</h2><p className="data-subtitle">Page {Math.floor(loaderData.offset / loaderData.limit) + 1} · {loaderData.items.length} records loaded</p></div></div>
          {filtered.length === 0 ? <div className="empty-state"><strong>{loaderData.items.length ? "No conversations match these filters" : "No conversations on this page"}</strong><p>Adjust your search or filters, or move to another page.</p></div> : <div className="table-scroll"><table><thead><tr><th>Conversation / contact</th><th>Channel</th><th>Status</th><th>Workflow state</th><th>Started</th><th>Last updated</th></tr></thead><tbody>
            {filtered.map((item) => <tr key={item.id}><td><strong>{shortId(item.id)}</strong><small>Contact {shortId(item.contact_id)}</small></td><td><span className="channel-label">{readable(item.channel)}</span></td><td><span className="status-chip">{readable(item.status)}</span></td><td>{readable(item.workflow_state)}</td><td>{dateLabel(item.started_at)}</td><td>{dateLabel(item.updated_at)}</td></tr>)}
          </tbody></table></div>}
        </section>
        <nav className="pagination" aria-label="Conversation pages"><span>Showing records {loaderData.items.length ? loaderData.offset + 1 : 0}–{loaderData.offset + loaderData.items.length}</span><div>{loaderData.offset > 0 && <Link className="secondary-button" to={`/conversations?offset=${previous}`}>Previous</Link>}{loaderData.hasNext && <Link className="primary-button" to={`/conversations?offset=${next}`}>Next page</Link>}</div></nav>
        <p className="workspace-footer">Search and filters apply to the current page of up to {PAGE_SIZE} records. Use pagination to search other pages; the API does not currently expose server-side search or a total count.</p>
      </>}
    </main>
  );
}
