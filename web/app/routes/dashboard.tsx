import { useState, type FormEvent } from "react";
import type { MetaFunction } from "@remix-run/node";

export const meta: MetaFunction = () => [
  { title: "Workspace | Agent Platform" },
  { name: "description", content: "Review tenant-scoped leads and receptionist conversations." },
];

type Lead = {
  id: string;
  contact_id: string;
  status: string;
  created_at: string;
  scores?: { priority_score?: number | null } | null;
};

type Conversation = {
  id: string;
  business_id: string;
  contact_id: string;
  channel: string;
  status: string;
  workflow_state: string;
  assigned_staff_id: string | null;
  updated_at: string;
};

type ListResponse<T> = { items: T[]; offset: number; limit: number };

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Date unavailable"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function shortId(value: string) {
  return value.length > 15 ? value.slice(0, 8) + "…" + value.slice(-4) : value;
}

export default function DashboardRoute() {
  const [apiKey, setApiKey] = useState("");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  async function request<T>(path: string): Promise<T> {
    if (!API_BASE) {
      throw new Error("The frontend API address is not configured. Set VITE_PLATFORM_API_URL and rebuild the web app.");
    }
    const response = await fetch(API_BASE + path, {
      headers: { Authorization: "Bearer " + apiKey, Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof payload.detail === "string" ? payload.detail : "Request failed (" + response.status + ").";
      throw new Error(detail);
    }
    return payload as T;
  }

  async function loadWorkspace(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!apiKey.trim()) {
      setNotice("Enter an API key provisioned for your tenant.");
      return;
    }
    setLoading(true);
    setNotice("");
    try {
      const [leadResult, conversationResult] = await Promise.all([
        request<ListResponse<Lead>>("/leads?offset=0&limit=25"),
        request<ListResponse<Conversation>>("/conversations?offset=0&limit=25"),
      ]);
      setLeads(leadResult.items);
      setConversations(conversationResult.items);
      setLoaded(true);
      setNotice("Workspace refreshed.");
    } catch (error) {
      setNotice(error instanceof Error
        ? error.message
        : "Could not load the workspace. Check the API address and your key permissions.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="workspace-shell">
      <header className="workspace-heading">
        <div>
          <p className="eyebrow">Tenant workspace</p>
          <h1>Your receptionist activity</h1>
          <p className="workspace-description">
            Review recent leads and conversations. The API scopes every result to the tenant assigned to your key.
          </p>
        </div>
        {loaded && <button className="secondary-button refresh-button" type="button" disabled={loading} onClick={() => void loadWorkspace()}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>}
      </header>

      <section className="card credentials-card workspace-credentials">
        <div className="section-heading">
          <span className="number">1</span>
          <div><h2>Connect to your workspace</h2><p>Your API key is used only in this page session and is not saved in browser storage.</p></div>
        </div>
        <form className="key-form" onSubmit={(event) => void loadWorkspace(event)}>
          <label htmlFor="workspace-api-key">Provisioned API key</label>
          <div className="key-entry">
            <input
              id="workspace-api-key"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="ap_…"
            />
            <button className="primary-button" type="submit" disabled={loading || !apiKey.trim()}>
              {loading ? "Loading…" : loaded ? "Reconnect" : "Connect"}
            </button>
          </div>
          <p className="helper">Keys must be provisioned by a trusted administrator with lead_reader and conversation access. Tenant identity comes from the verified key, never from a form field.</p>
        </form>
        {notice && <p className="notice" role="status">{notice}</p>}
      </section>

      <section className="workspace-stats" aria-label="Workspace summary">
        <article className="stat-card"><span>Recent leads</span><strong>{loaded ? leads.length : "—"}</strong><small>{loaded ? "Up to 25 most recent" : "Connect to load"}</small></article>
        <article className="stat-card"><span>Conversations</span><strong>{loaded ? conversations.length : "—"}</strong><small>{loaded ? "Up to 25 most recent" : "Connect to load"}</small></article>
        <article className="stat-card"><span>Connection</span><strong className={loaded ? "connection-ready" : "connection-idle"}>{loaded ? "Connected" : "Not connected"}</strong><small>{loaded ? "API data loaded" : "No key stored"}</small></article>
      </section>

      <div className="workspace-grid">
        <section className="card data-card">
          <div className="data-heading"><div><p className="eyebrow">Lead management</p><h2>Recent leads</h2></div><span className="data-count">{loaded ? leads.length : "—"}</span></div>
          {!loaded ? <p className="empty-state">Connect your API key to view lead records.</p> : leads.length === 0 ? <p className="empty-state">No leads are available for this tenant yet.</p> : (
            <div className="table-scroll"><table><thead><tr><th>Lead</th><th>Status</th><th>Priority</th><th>Created</th></tr></thead>
              <tbody>{leads.map((lead) => <tr key={lead.id}>
                <td><strong>{shortId(lead.id)}</strong><small>Contact {shortId(lead.contact_id)}</small></td>
                <td><span className="status-chip">{lead.status.replaceAll("_", " ")}</span></td>
                <td>{typeof lead.scores?.priority_score === "number" ? lead.scores.priority_score : "—"}</td>
                <td>{formatDate(lead.created_at)}</td>
              </tr>)}</tbody>
            </table></div>
          )}
        </section>

        <section className="card data-card">
          <div className="data-heading"><div><p className="eyebrow">Receptionist inbox</p><h2>Recent conversations</h2></div><span className="data-count">{loaded ? conversations.length : "—"}</span></div>
          {!loaded ? <p className="empty-state">Connect your API key to view conversation records.</p> : conversations.length === 0 ? <p className="empty-state">No conversations are available for this tenant yet.</p> : (
            <div className="table-scroll"><table><thead><tr><th>Conversation</th><th>Channel</th><th>Status</th><th>Updated</th></tr></thead>
              <tbody>{conversations.map((conversation) => <tr key={conversation.id}>
                <td><strong>{shortId(conversation.id)}</strong><small>Contact {shortId(conversation.contact_id)}</small></td>
                <td>{conversation.channel}</td>
                <td><span className="status-chip">{conversation.status.replaceAll("_", " ")}</span><small>{conversation.workflow_state.replaceAll("_", " ")}</small></td>
                <td>{formatDate(conversation.updated_at)}</td>
              </tr>)}</tbody>
            </table></div>
          )}
        </section>
      </div>
      <footer className="workspace-footer">For setup and provider checks, use the onboarding flow. Sending and campaign controls are intentionally not exposed until their provider and permission gates are configured.</footer>
    </main>
  );
}
