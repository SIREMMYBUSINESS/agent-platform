import { Link, redirect, useNavigation } from "react-router";
import { readSessionApiKey } from "../.server/session";
import type { Route } from "./+types/contact-conversations";

type Conversation = {
  id: string;
  channel: string;
  status: string;
  workflow_state: string;
  started_at: string;
  updated_at: string;
};
type TimelineItem = {
  id: string;
  kind: "message" | "event";
  conversation_id: string;
  created_at: string;
  channel?: string | null;
  direction?: string | null;
  sender?: string | null;
  subject?: string | null;
  content?: string | null;
  delivery_status?: string | null;
  event_type?: string | null;
  payload?: Record<string, unknown> | null;
  source?: string | null;
};
type ConversationListResponse = {
  items: Conversation[];
  conversations_included: number;
  conversation_limit: number;
  conversations_truncated: boolean;
};
type TimelineResponse = {
  items: TimelineItem[];
  limit: number;
  older_cursor: string | null;
  newer_cursor: string | null;
  conversations_included: number;
  conversation_limit: number;
  conversations_truncated: boolean;
};

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");
const PAGE_SIZE = 25;

export function meta() {
  return [
    { title: "Contact timeline | Staging Workspace | Agent Platform" },
    { name: "description", content: "Read the tenant-scoped conversation history and handoff events for a contact." },
    { name: "robots", content: "noindex, nofollow" },
  ];
}
export function headers() { return { "Cache-Control": "private, no-store, max-age=0" }; }

export async function loader({ request, context, params }: Route.LoaderArgs) {
  const contactId = params.contactId;
  if (!contactId) throw new Response("Contact not found", { status: 404 });
  const url = new URL(request.url);
  const returnTo = "/contacts/" + encodeURIComponent(contactId) + "/conversations" + url.search;
  const secret = context.cloudflare.env.SESSION_ENCRYPTION_KEY;
  const apiKey = secret ? await readSessionApiKey(request, secret) : null;
  if (!apiKey) return redirect("/login?returnTo=" + encodeURIComponent(returnTo));
  if (!API_BASE) {
    return {
      contactId, conversations: null as ConversationListResponse | null,
      timeline: null as TimelineResponse | null, error: "The staging API URL is not configured.",
    };
  }
  try {
    const headers = { Authorization: "Bearer " + apiKey, Accept: "application/json" };
    const contactPath = "/contacts/" + encodeURIComponent(contactId);
    const timelineUrl = new URL(API_BASE + contactPath + "/timeline");
    timelineUrl.searchParams.set("limit", String(PAGE_SIZE));
    const cursor = url.searchParams.get("cursor");
    if (cursor) timelineUrl.searchParams.set("cursor", cursor);
    const [conversationsResponse, timelineResponse] = await Promise.all([
      fetch(API_BASE + contactPath + "/conversations", { headers, redirect: "follow" }),
      fetch(timelineUrl, { headers, redirect: "follow" }),
    ]);
    for (const response of [conversationsResponse, timelineResponse]) {
      if (response.url && new URL(response.url).origin !== new URL(API_BASE).origin) {
        return { contactId, conversations: null, timeline: null, error: "The staging API redirected to an unexpected host." };
      }
      if (response.status === 401) return redirect("/login?reason=expired&returnTo=" + encodeURIComponent(returnTo));
      if (response.status === 404) return { contactId, conversations: null, timeline: null, error: "This contact is not available in the active tenant and business scope." };
    }
    if (!conversationsResponse.ok || !timelineResponse.ok) {
      const status = !conversationsResponse.ok ? conversationsResponse.status : timelineResponse.status;
      return { contactId, conversations: null, timeline: null, error: "The contact timeline service returned HTTP " + status + "." };
    }
    const conversations = await conversationsResponse.json() as ConversationListResponse;
    const timeline = await timelineResponse.json() as TimelineResponse;
    if (!Array.isArray(conversations.items) || !Array.isArray(timeline.items)) throw new Error("Invalid response");
    return { contactId, conversations, timeline, error: null as string | null };
  } catch {
    return { contactId, conversations: null, timeline: null, error: "Could not load the contact timeline. Check API availability and retry." };
  }
}

function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function shortId(value: string) { return value.length > 16 ? value.slice(0, 8) + "…" + value.slice(-4) : value; }
function readable(value: string) { return value.replaceAll("_", " ").replaceAll("-", " "); }
function cursorPath(cursor: string) { return "?" + new URLSearchParams({ cursor }).toString(); }

export default function ContactConversationsRoute({ loaderData }: Route.ComponentProps) {
  const navigation = useNavigation();
  const isLoading = navigation.state === "loading";
  const conversations = loaderData.conversations;
  const timeline = loaderData.timeline;

  return (
    <main className="workspace-shell workspace-list-page conversations-page" aria-busy={isLoading}>
      <header className="workspace-heading">
        <div><p className="eyebrow">Tenant workspace · Inbox</p><h1>Contact timeline</h1><p className="workspace-description">Contact {shortId(loaderData.contactId)} · Read-only history across this contact’s conversations.</p></div>
        <div className="workspace-actions workspace-segmented-control" role="group" aria-label="Workspace shortcuts"><Link className="secondary-button" to="/conversations">Conversations</Link><Link className="secondary-button" to="/leads">Leads</Link></div>
      </header>
      {isLoading && <p className="submit-status" role="status">Loading contact timeline…</p>}
      {loaderData.error ? <section className="card workspace-credentials" role="alert"><h2>Contact timeline unavailable</h2><p>{loaderData.error}</p><Link className="primary-button" to="/conversations">Return to conversations</Link></section> : conversations && timeline ? <>
        {timeline.conversations_truncated && <p className="page-scope-note" role="status">This contact has more than {timeline.conversation_limit} conversations. The timeline is limited to the newest {timeline.conversation_limit}; older conversations are not included.</p>}
        <section className="card data-card">
          <div className="data-heading"><div><p className="eyebrow">Contact history</p><h2>{conversations.conversations_included} conversations loaded</h2><p className="data-subtitle">{timeline.items.length} timeline entries on this page</p></div></div>
          <div className="table-scroll"><table><thead><tr><th>Conversation</th><th>Channel</th><th>Status</th><th>Started</th></tr></thead><tbody>
            {conversations.items.map((item) => <tr key={item.id}><td>{shortId(item.id)}</td><td><span className="channel-label">{readable(item.channel)}</span></td><td><span className="status-chip">{readable(item.status)}</span></td><td>{dateLabel(item.started_at)}</td></tr>)}
          </tbody></table></div>
        </section>
        <section className="card data-card">
          <div className="data-heading"><div><p className="eyebrow">Unified activity</p><h2>Messages and events</h2><p className="data-subtitle">Ordered by creation time, then record ID</p></div></div>
          {timeline.items.length === 0 ? <div className="empty-state"><strong>No timeline entries on this page</strong><p>There are no messages or events in the selected conversation history for this page.</p></div> : <ol className="contact-timeline">
            {timeline.items.map((item) => <li key={item.kind + ":" + item.id} className="contact-timeline-item">
              <div className="contact-timeline-meta"><strong>{item.kind === "message" ? readable(item.channel || "message") + " · " + readable(item.direction || "message") : item.event_type === "human.escalation.alert" ? "Human escalation alert" : readable(item.event_type || "event")}</strong><time dateTime={item.created_at}>{dateLabel(item.created_at)}</time></div>
              {item.kind === "message" ? <><p className="contact-timeline-sender">{item.sender || "Unknown sender"}{item.delivery_status ? " · " + readable(item.delivery_status) : ""}</p>{item.subject && <h3>{item.subject}</h3>}<p className="contact-timeline-content">{item.content || "(empty message)"}</p></> : <p className="contact-timeline-content">{typeof item.payload?.reason === "string" ? item.payload.reason : item.event_type === "human.escalation.alert" ? "A human handoff was raised for this conversation." : "Conversation event recorded."}</p>}
              <small>Conversation {shortId(item.conversation_id)}</small>
            </li>)}
          </ol>}
          <nav className="pagination" aria-label="Contact timeline pages">
            <div>
              {timeline.newer_cursor ? <Link className="secondary-button" to={cursorPath(timeline.newer_cursor)}>Newer</Link> : <button className="secondary-button" type="button" disabled>Newer</button>}
              {timeline.older_cursor ? <Link className="primary-button" to={cursorPath(timeline.older_cursor)}>Older</Link> : <button className="primary-button" type="button" disabled>Older</button>}
            </div>
          </nav>
        </section>
      </> : null}
    </main>
  );
}
