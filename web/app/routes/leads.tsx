import { useMemo, useState } from "react";
import { Link, redirect, useNavigation } from "react-router";
import { readSessionApiKey } from "../.server/session";
import type { Route } from "./+types/leads";

type Lead = { id: string; contact_id: string; status: string; created_at: string; scores?: { fit_score?: number; urgency_score?: number; intent_score?: number; priority_score?: number } | null };
type ListResponse<T> = { items: T[]; offset: number; limit: number };
const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");
const PAGE_SIZE = 25;
export function meta() { return [{ title: "Lead pipeline | Staging Workspace | Agent Platform" }, { name: "description", content: "Review, search, and filter recent tenant-scoped leads in the Agent Platform staging workspace." }, { name: "robots", content: "noindex, nofollow" }]; }
export function headers() { return { "Cache-Control": "private, no-store, max-age=0" }; }
export async function loader({ request, context }: Route.LoaderArgs) {
  const secret = context.cloudflare.env.SESSION_ENCRYPTION_KEY;
  const apiKey = secret ? await readSessionApiKey(request, secret) : null;
  if (!apiKey) return redirect("/login?returnTo=%2Fleads");
  const url = new URL(request.url);
  const rawOffset = Number(url.searchParams.get("offset") || 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  if (!API_BASE) return { items: [] as Lead[], offset, limit: PAGE_SIZE, hasNext: false, error: "The staging API URL is not configured." };
  try {
    const response = await fetch(`${API_BASE}/leads?offset=${offset}&limit=${PAGE_SIZE}`, { headers: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" }, redirect: "follow" });
    if (response.url && new URL(response.url).origin !== new URL(API_BASE).origin) return { items: [] as Lead[], offset, limit: PAGE_SIZE, hasNext: false, error: "The staging API redirected to an unexpected host." };
    if (response.status === 401) return redirect("/login?reason=expired&returnTo=%2Fleads");
    if (!response.ok) return { items: [] as Lead[], offset, limit: PAGE_SIZE, hasNext: false, error: response.status === 403 ? "This API key does not have permission to read leads for this tenant." : `The leads service returned HTTP ${response.status}.` };
    const payload = await response.json() as ListResponse<Lead>;
    if (!payload || !Array.isArray(payload.items)) throw new Error("Invalid response");
    return { items: payload.items, offset, limit: PAGE_SIZE, hasNext: payload.items.length === PAGE_SIZE, error: null as string | null };
  } catch { return { items: [] as Lead[], offset, limit: PAGE_SIZE, hasNext: false, error: "Could not load leads. Check staging API availability and retry." }; }
}
function dateLabel(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "Date unavailable" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date); }
function shortId(value: string) { return value.length > 16 ? value.slice(0, 8) + "…" + value.slice(-4) : value; }
function readable(value: string) { return value.replaceAll("_", " ").replaceAll("-", " "); }
function csvCell(value: unknown) { const text = value == null ? "" : String(value); return `"${text.replaceAll('"', '""')}"`; }
function exportLeadsCsv(leads: Lead[]) {
  const rows = [["Lead ID", "Contact ID", "Status", "Priority Score", "Fit Score", "Urgency Score", "Intent Score", "Created"], ...leads.map((lead) => [lead.id, lead.contact_id, lead.status, lead.scores?.priority_score, lead.scores?.fit_score, lead.scores?.urgency_score, lead.scores?.intent_score, lead.created_at])];
  const csv = rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a"); link.href = url; link.download = "leads-current-page.csv"; link.click(); URL.revokeObjectURL(url);
}
export default function LeadsRoute({ loaderData }: Route.ComponentProps) {
  const navigation = useNavigation(); const isLoading = navigation.state === "loading";
  const [query, setQuery] = useState(""); const [status, setStatus] = useState("all"); const [priority, setPriority] = useState("all");
  const statuses = useMemo(() => [...new Set(loaderData.items.map((item) => item.status))].sort(), [loaderData.items]);
  const filtered = useMemo(() => loaderData.items.filter((lead) => {
    const search = query.trim().toLocaleLowerCase();
    const matchesSearch = !search || [lead.id, lead.contact_id, lead.status].some((v) => v.toLocaleLowerCase().includes(search));
    const matchesStatus = status === "all" || lead.status === status; const score = lead.scores?.priority_score;
    const matchesPriority = priority === "all" || (priority === "high" ? typeof score === "number" && score >= 70 : priority === "medium" ? typeof score === "number" && score >= 40 && score < 70 : typeof score === "number" && score < 40);
    return matchesSearch && matchesStatus && matchesPriority;
  }), [loaderData.items, query, status, priority]);
  const hasFilters = Boolean(query.trim() || status !== "all" || priority !== "all");
  const previous = Math.max(0, loaderData.offset - loaderData.limit); const next = loaderData.offset + loaderData.limit;
  const page = Math.floor(loaderData.offset / loaderData.limit) + 1; const rangeStart = loaderData.items.length ? loaderData.offset + 1 : 0; const rangeEnd = loaderData.offset + loaderData.items.length;
  return <main className="workspace-shell workspace-list-page leads-page" aria-busy={isLoading}>
    <header className="workspace-heading"><div><p className="eyebrow">Tenant workspace · Pipeline</p><h1>Lead pipeline</h1><p className="workspace-description">Search and review the latest leads assigned to this tenant.</p></div><div className="workspace-actions workspace-segmented-control" role="group" aria-label="Workspace shortcuts"><Link className="secondary-button" to="/dashboard">Overview</Link><Link className="secondary-button" to="/conversations">Conversations</Link></div></header>
    {isLoading && <p className="submit-status" role="status">Loading leads…</p>}
    {loaderData.error ? <section className="card workspace-credentials" role="alert"><h2>Leads unavailable</h2><p>{loaderData.error}</p><Link className="primary-button" to="/dashboard">Return to workspace</Link></section> : <>
      <section className="card list-toolbar" aria-label="Lead filters"><label className="workspace-search"><span className="sr-only">Search this page of leads</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search this page…" /></label><label className="filter-control">Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All statuses</option>{statuses.map((value) => <option key={value} value={value}>{readable(value)}</option>)}</select></label><label className="filter-control">Priority<select value={priority} onChange={(event) => setPriority(event.target.value)}><option value="all">All priorities</option><option value="high">High · 70+</option><option value="medium">Medium · 40–69</option><option value="low">Low · below 40</option></select></label><button className="secondary-button leads-export" type="button" onClick={() => exportLeadsCsv(filtered)} disabled={filtered.length === 0}>Export CSV</button></section>
      <section className="card data-card"><div className="data-heading leads-data-heading"><div><p className="eyebrow">Lead management</p><h2>Recent leads <details className="leads-info"><summary aria-label="About lead search and pagination" title="About lead search and pagination">ⓘ</summary><div className="leads-info-popover">Search and filters apply to the current page of up to {PAGE_SIZE} records. Use Previous and Next to browse pages. Server-side search and a total count are not available yet.</div></details></h2><p className="data-subtitle">Page {page} · {loaderData.items.length} records loaded</p></div><span className="data-count leads-count">{rangeStart}–{rangeEnd} of this page</span></div>
      {filtered.length === 0 ? <div className="empty-state leads-empty"><strong>{loaderData.items.length === 0 && !hasFilters ? "No leads yet" : loaderData.items.length === 0 ? "No leads on this page" : "No leads match these filters"}</strong><p>{loaderData.items.length === 0 && !hasFilters ? "Connect a channel to start capturing leads." : loaderData.items.length === 0 ? "Use the page controls to check other pages." : "Try clearing a filter or changing your search."}</p>{loaderData.items.length === 0 && !hasFilters && <Link className="empty-state-link" to="/onboarding">Connect a channel</Link>}</div> : <div className="table-scroll"><table><thead><tr><th>Lead / contact</th><th>Status</th><th>Priority</th><th>Fit</th><th>Urgency</th><th>Created</th></tr></thead><tbody>{filtered.map((lead) => { const score = lead.scores?.priority_score; const level = typeof score !== "number" ? "Not scored" : score >= 70 ? "High" : score >= 40 ? "Medium" : "Low"; return <tr key={lead.id}><td><strong>{shortId(lead.id)}</strong><small>Contact {shortId(lead.contact_id)}</small></td><td><span className="status-chip">{readable(lead.status)}</span></td><td><span className={`priority-chip priority-${level.toLowerCase().replace(" ", "-")}`}>{typeof score === "number" ? `${score} · ${level}` : level}</span></td><td>{lead.scores?.fit_score ?? "—"}</td><td>{lead.scores?.urgency_score ?? "—"}</td><td>{dateLabel(lead.created_at)}</td></tr>; })}</tbody></table></div>}</section>
      <nav className="pagination leads-pagination" aria-label="Lead pages"><span>Showing records {rangeStart}–{rangeEnd}</span><div><Link className={loaderData.offset > 0 ? "secondary-button" : "secondary-button is-disabled"} aria-disabled={loaderData.offset === 0} tabIndex={loaderData.offset === 0 ? -1 : undefined} to={loaderData.offset > 0 ? `/leads?offset=${previous}` : "#"}>Previous</Link><span className="page-indicator">Page {page}</span>{loaderData.hasNext ? <Link className="primary-button" to={`/leads?offset=${next}`}>Next</Link> : <button className="primary-button" type="button" disabled>Next</button>}</div></nav>
    </>}
  </main>;
}
