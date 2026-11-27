import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import type { Route } from "./+types/_index";
import { readSessionCredential } from "../.server/session";

type Question = { key: string; prompt: string; answer_type: string; required: boolean };
type Hour = { weekday: number; opens_at: string; closes_at: string; active: boolean };
type Service = { name: string; duration_minutes: number };
type Staff = { display_name: string; email: string };
type Template = {
  vertical_id: string;
  display_name: string;
  intake_questions: Question[];
  business_hours: Hour[];
  services: Service[];
  message_templates: Record<string, string>;
  template_variables_by_key: Record<string, string[]>;
  required_providers: string[];
  optional_providers: string[];
};
type Health = { ready: boolean; checks: Record<string, boolean>; provider_status: Record<string, string>; template_warnings?: string[] };
type ProviderSelection = { sms: string; calendar: string; email?: string; crm?: string };

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");
const DEFAULT_GREETING = "Hello {{contact_name}}, thanks for contacting our team.";
const DEFAULT_OUTREACH = "Hello {{contact_name}}, we're following up on your enquiry. Reply to continue.";
function validateMessageTemplate(template: string, allowedVariables: ReadonlySet<string>): string[] {
  const variables = [...template.matchAll(/{{\s*([A-Za-z][A-Za-z0-9_]*)\s*}}/g)].map((match) => match[1]);
  const residue = template.replace(/{{\s*[A-Za-z][A-Za-z0-9_]*\s*}}/g, "");
  const errors: string[] = [];
  if (residue.includes("{{") || residue.includes("}}")) errors.push("Malformed template variable syntax.");
  const unsupported = [...new Set(variables.filter((name) => !allowedVariables.has(name)))];
  if (unsupported.length) errors.push("Unsupported variable(s): " + unsupported.map((name) => "{{" + name + "}}").join(", "));
  return errors;
}
function renderIdentityPreview(template: string, messageKey: "greeting" | "outreach", allowedVariables: string[], businessName: string, staffName: string): string {
  const errors = validateMessageTemplate(template, new Set(allowedVariables));
  if (errors.length) return "Preview unavailable: " + errors.join(" ");
  let rendered = template.replace(/{{\s*contact_name\s*}}/g, "Sample contact");
  if (messageKey === "greeting") {
    if (!businessName.trim()) return "Enter the business name to preview this message.";
    if (!rendered.includes("our team")) return "Preview unavailable: greeting is missing the business-name marker.";
    return rendered.replaceAll("our team", businessName.trim());
  }
  if (!staffName.trim()) return "Add a staff member to preview the sign-off.";
  return rendered.trimEnd() + " — " + staffName.trim();
}

class PlatformApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function callPlatform(path: string, apiKey: string, method = "GET", body?: unknown) {
  if (!API_BASE) throw new Error("The platform API is not configured for this environment.");
  const response = await fetch(API_BASE + path, {
    method,
    headers: {
      Authorization: `Bearer ${apiKey}`,
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (response.url && new URL(response.url).origin !== new URL(API_BASE).origin) {
    throw new Error("The platform API redirected to an unexpected host.");
  }
  const payload = await response.json().catch(() => ({})) as { detail?: unknown };
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status})`;
    throw new PlatformApiError(response.status, detail);
  }
  return payload;
}

export async function loader({ request, context }: Route.LoaderArgs) {
  const apiKey = await readSessionCredential(request, context.cloudflare.env.SESSION_ENCRYPTION_KEY);
  if (!apiKey) return {
    authenticated: false, canOnboard: false, requiresOnboardRole: false,
    accessError: null as string | null,
  };
  try {
    await callPlatform("/verticals", apiKey);
    return { authenticated: true, canOnboard: true, requiresOnboardRole: false, accessError: null as string | null };
  } catch (error) {
    const missingRole = error instanceof PlatformApiError
      && error.status === 403
      && error.message === "Operation not permitted";
    const unauthorized = error instanceof PlatformApiError && error.status === 401;
    const denied = error instanceof PlatformApiError && error.status === 403;
    return {
      authenticated: true,
      canOnboard: false,
      requiresOnboardRole: missingRole,
      accessError: missingRole
        ? "Your API key is valid, but its tenant does not have the business_onboarder permission required to configure a business. Ask a workspace owner to review the key's role grants."
        : unauthorized
          ? "Your workspace session has expired or was revoked. Reconnect with your provisioned staging API key."
          : denied
            ? "The staging API denied this request because of the tenant's access or region policy. Ask an administrator to review the staging grant and region configuration."
            : "The staging API could not be reached for onboarding. Check that the API is available, then reload this page.",
    };
  }
}

export function meta() {
  return [
    { title: "Workspace setup | Staging | Agent Platform" },
    { name: "description", content: "Configure a tenant workspace and review staging readiness checks in Agent Platform." },
    { name: "robots", content: "noindex, nofollow" },
  ];
}

export function headers() {
  return { "Cache-Control": "private, no-store, max-age=0" };
}

export default function OnboardingRoute({ loaderData }: Route.ComponentProps) {
  const [template, setTemplate] = useState<Template | null>(null);
  const [verticals, setVerticals] = useState<Array<{ vertical_id: string; display_name?: string }>>([]);
  const [selectedVerticalId, setSelectedVerticalId] = useState("");
  const [loadingVerticals, setLoadingVerticals] = useState(false);
  const [businessName, setBusinessName] = useState("");
  const [timezone, setTimezone] = useState("UTC");
  const [questions, setQuestions] = useState<Question[]>([]);
  const [hours, setHours] = useState<Hour[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [providerSelection, setProviderSelection] = useState<ProviderSelection>({ sms: "twilio", calendar: "google" });
  const [staff, setStaff] = useState<Staff[]>([{ display_name: "", email: "" }]);
  const [businessId, setBusinessId] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const readyLabel = useMemo(() => health?.ready ? "Ready for go-live" : "Setup needs attention", [health]);
  const greetingAllowedVariables = template?.template_variables_by_key?.greeting || [];
  const outreachAllowedVariables = template?.template_variables_by_key?.outreach || [];
  const greetingErrors = validateMessageTemplate(messages.greeting || "", new Set(greetingAllowedVariables));
  const outreachErrors = validateMessageTemplate(messages.outreach || "", new Set(outreachAllowedVariables));
  const templateErrors = [...greetingErrors, ...outreachErrors];
  const primaryStaffName = staff.find((person) => person.display_name.trim())?.display_name.trim() || "";
  const greetingPreview = renderIdentityPreview(
    messages.greeting || DEFAULT_GREETING, "greeting", greetingAllowedVariables, businessName, primaryStaffName,
  );
  const followUpPreview = renderIdentityPreview(
    messages.outreach || DEFAULT_OUTREACH, "outreach", outreachAllowedVariables, businessName, primaryStaffName,
  );

  async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch("/api/onboarding", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path,
        method: init.method || "GET",
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      }),
    });
    const payload = await response.json().catch(() => ({})) as { detail?: unknown };
    if (!response.ok) {
      const detail = typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status})`;
      throw new Error(detail);
    }
    return payload as T;
  }

  async function loadVerticals() {
    setLoadingVerticals(true);
    try {
      const choices = await api<{ items: Array<{ vertical_id: string; display_name?: string }> }>("/verticals");
      setVerticals(choices.items);
      setSelectedVerticalId((current) => current || choices.items[0]?.vertical_id || "");
      if (choices.items.length === 0) setNotice("No business templates are available for this workspace yet.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not load business types.");
    } finally {
      setLoadingVerticals(false);
    }
  }

  useEffect(() => {
    if (loaderData.canOnboard) void loadVerticals();
  }, [loaderData.canOnboard]);

  useEffect(() => {
    try {
      const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (localTimezone) setTimezone(localTimezone);
    } catch {
      // Keep the stable UTC default when the browser cannot determine its timezone.
    }
  }, []);

  async function loadTemplate() {
    if (!selectedVerticalId) {
      setNotice("No business templates are currently available.");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const data = await api<Template>(`/verticals/${selectedVerticalId}/template`);
      setTemplate(data);
      setQuestions(data.intake_questions);
      setHours(data.business_hours);
      setServices(data.services);
      const rawGreeting = data.message_templates.greeting || DEFAULT_GREETING;
      const rawOutreach = data.message_templates.outreach || DEFAULT_OUTREACH;
      const greetingAllowed = new Set(data.template_variables_by_key?.greeting || []);
      const outreachAllowed = new Set(data.template_variables_by_key?.outreach || []);
      const greetingNeedsRepair = validateMessageTemplate(rawGreeting, greetingAllowed).length > 0
        || !rawGreeting.includes("our team");
      const outreachNeedsRepair = validateMessageTemplate(rawOutreach, outreachAllowed).length > 0;
      setMessages({
        ...data.message_templates,
        greeting: greetingNeedsRepair ? DEFAULT_GREETING : rawGreeting,
        outreach: outreachNeedsRepair ? DEFAULT_OUTREACH : rawOutreach,
      });
      setStaff((current) => current.length ? current : [{ display_name: "", email: "" }]);
      setProviderSelection({ sms: "twilio", calendar: "google" });
      setNotice((data.display_name || selectedVerticalId) + " defaults loaded. Review and customize your setup."
        + (greetingNeedsRepair || outreachNeedsRepair ? " An incompatible identity template was replaced with a safe default." : ""));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not load the template.");
    } finally {
      setBusy(false);
    }
  }

  async function createAndCheck() {
    if (!template || !businessName.trim() || !staff.some((person) => person.display_name.trim())) {
      setNotice("Enter a business name and at least one staff member.");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const created = await api<{ business_id: string }>("/onboarding/businesses", {
        method: "POST",
        body: JSON.stringify({
          vertical_id: template.vertical_id,
          business_name: businessName,
          timezone,
          intake_questions: questions,
          business_hours: hours,
          services,
          message_templates: messages,
          staff: staff.filter((person) => person.display_name.trim()),
          provider_selection: providerSelection,
        }),
      });
      setBusinessId(created.business_id);
      const result = await api<Health>(`/businesses/${created.business_id}/onboarding/health`);
      setHealth(result);
      setNotice(result.ready
        ? "Setup is healthy. Review the checks, then bring the business online."
        : "Business saved. Complete the remaining checks before go-live.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not create the business.");
    } finally {
      setBusy(false);
    }
  }

  async function runHealthCheck() {
    if (!businessId) return;
    setBusy(true);
    try {
      setHealth(await api<Health>(`/businesses/${businessId}/onboarding/health`));
      setNotice("Health check refreshed.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Health check failed.");
    } finally {
      setBusy(false);
    }
  }

  async function goLive() {
    if (!businessId || !health?.ready) return;
    setBusy(true);
    try {
      await api(`/businesses/${businessId}/onboarding/go-live`, { method: "POST" });
      setNotice("The business is live.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Go-live check failed.");
    } finally {
      setBusy(false);
    }
  }

  function updateQuestion(index: number, prompt: string) {
    setQuestions((current) => current.map((question, i) => i === index ? { ...question, prompt } : question));
  }

  function updateHour(index: number, patch: Partial<Hour>) {
    setHours((current) => current.map((hour, i) => i === index ? { ...hour, ...patch } : hour));
  }

  function updateService(index: number, patch: Partial<Service>) {
    setServices((current) => current.map((service, i) => i === index ? { ...service, ...patch } : service));
  }

  return (
    <main className="page-shell">
      <header className="topbar">
        <div className="brand-mark">A</div>
        <div><p className="eyebrow">Agent Platform</p><h1>Business onboarding</h1></div>
        <span className="step-pill">About 5–10 minutes</span>
      </header>

      <section className="intro">
        <p className="eyebrow">Workspace setup</p>
        <h2>Set up your digital front desk</h2>
        <p>Choose a business template, tailor it to your team, and complete the readiness checks before bringing the workflow online.</p>
      </section>

      {!loaderData.canOnboard ? <section className="card credentials-card">
        <div className="section-heading"><span className="number">1</span><div><h3>{loaderData.authenticated ? loaderData.requiresOnboardRole ? "Onboarding permission needed" : "Onboarding unavailable" : "Connect to your workspace"}</h3><p>{loaderData.accessError || "Use your workspace session to continue onboarding."}</p></div></div>
        {loaderData.authenticated
          ? loaderData.requiresOnboardRole && <p className="helper">Your API-key session is valid. A workspace owner can add the <code>business_onboarder</code> permission to this tenant key.</p>
          : <Link className="primary-button" to="/login?returnTo=%2Fonboarding">Connect with API key</Link>}
      </section> : <>
        <section className="card credentials-card">
          <div className="section-heading"><span className="number">1</span><div><h3>Workspace connected</h3><p>Onboarding uses your encrypted workspace session.</p></div></div>
          <p className="helper">Provider credentials belong in the protected secret manager, not this page.</p>
          {loadingVerticals && <p className="notice" role="status">Loading available business types…</p>}
          {notice && !template && !loadingVerticals && <p className="notice" role="status">{notice}</p>}
          <div className="field-grid field-row">
            <label>Business type
              <select value={selectedVerticalId} onChange={(event) => { setSelectedVerticalId(event.target.value); setTemplate(null); setHealth(null); setBusinessId(""); }} disabled={busy || verticals.length === 0 || !!businessId}>
                {verticals.map((vertical) => <option key={vertical.vertical_id} value={vertical.vertical_id}>{vertical.display_name || vertical.vertical_id.replaceAll("_", " ")}</option>)}
              </select>
            </label>
          </div>
          <button className="secondary-button" disabled={busy || !selectedVerticalId} onClick={loadTemplate}>{busy ? "Loading…" : "Load selected template"}</button>
        </section>

      {template && <>
        <section className="card">
          <div className="section-heading"><span className="number">2</span><div><h3>Business details</h3><p>Business scope and local scheduling details.</p></div></div>
          <div className="field-grid field-row">
            <label>Business name<input value={businessName} onChange={(event) => setBusinessName(event.target.value)} placeholder="Your business name" /></label>
            <label>Business timezone<input value={timezone} onChange={(event) => setTimezone(event.target.value)} placeholder="Africa/Banjul" /></label>
          </div>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">3</span><div><h3>Intake questions</h3><p>Questions are asked one at a time and saved with the conversation.</p></div></div>
          <div className="stack">
            {questions.map((question, index) => <div key={question.key}>
              <label htmlFor={`intake-question-${question.key}`}>{index + 1}. Customer-facing question</label>
              <input id={`intake-question-${question.key}`} aria-label={`Question for customers: ${question.key.replaceAll("_", " ")}`} value={question.prompt} placeholder="Enter the question for customers." onChange={(event) => updateQuestion(index, event.target.value)} />
              <p className="helper">This text is the prompt customers will receive, not an answer.</p>
            </div>)}
          </div>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">4</span><div><h3>Business hours</h3><p>Times use the business's configured timezone.</p></div></div>
          <div className="hours-list">
            {hours.map((hour, index) => <div className="hours-row" key={hour.weekday}>
              <label className="day-check"><input type="checkbox" checked={hour.active} onChange={(event) => updateHour(index, { active: event.target.checked })} />{DAYS[hour.weekday]}</label>
              <div className="hours-times">
                <input aria-label={`${DAYS[hour.weekday]} opens at`} type="time" value={hour.opens_at} onChange={(event) => updateHour(index, { opens_at: event.target.value })} />
                <span>to</span>
                <input aria-label={`${DAYS[hour.weekday]} closes at`} type="time" value={hour.closes_at} onChange={(event) => updateHour(index, { closes_at: event.target.value })} />
              </div>
            </div>)}
          </div>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">5</span><div><h3>Services and team</h3><p>Service durations inform scheduling where enabled. At least one team member is required.</p></div></div>
          <div className="stack">
            {services.map((service, index) => <div className="field-grid field-row service-row" key={index}>
              <label>Service<input value={service.name} onChange={(event) => updateService(index, { name: event.target.value })} /></label>
              <label>Duration in minutes<input type="number" min={10} max={240} value={service.duration_minutes} onChange={(event) => updateService(index, { duration_minutes: Number(event.target.value) })} /></label>
            </div>)}
            {staff.map((member, index) => <div className="field-grid field-row" key={index}>
              <label>Staff name<input value={member.display_name} onChange={(event) => setStaff((current) => current.map((item, i) => i === index ? { ...item, display_name: event.target.value } : item))} placeholder="Team member" /></label>
              <label>Staff email<input type="email" value={member.email} onChange={(event) => setStaff((current) => current.map((item, i) => i === index ? { ...item, email: event.target.value } : item))} placeholder="Optional" /></label>
            </div>)}
            <button className="text-button" onClick={() => setStaff((current) => [...current, { display_name: "", email: "" }])}>+ Add staff member</button>
          </div>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">6</span><div><h3>Customer messages</h3><p>Use the supported variables provided by the selected business template.</p></div></div>
          <label>Greeting<textarea spellCheck={false} aria-invalid={greetingErrors.length > 0} value={messages.greeting || ""} onChange={(event) => setMessages((current) => ({ ...current, greeting: event.target.value }))} rows={2} />
            {greetingErrors.map((error) => <p className="field-error" role="alert" key={error}>{error}</p>)}
            <p className="helper">Only variables defined by the selected business type are accepted. The business name is inserted from the business record, outside the variable syntax.</p>
            <p className="helper">Preview: {greetingPreview}</p></label>
          <label>Follow-up message<textarea spellCheck={false} aria-invalid={outreachErrors.length > 0} value={messages.outreach || ""} onChange={(event) => setMessages((current) => ({ ...current, outreach: event.target.value }))} rows={3} />
            {outreachErrors.map((error) => <p className="field-error" role="alert" key={error}>{error}</p>)}
            <p className="helper">The sign-off comes from the active staff record at render time; it is not saved in the template text.</p>
            <p className="helper">Preview: {followUpPreview}</p>
            {templateErrors.length === 0 && <p className="helper">Template variables pass validation.</p>}</label>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">7</span><div><h3>Provider connections</h3><p>Select adapters here; add credentials in your protected provider secret manager.</p></div></div>
          <div className="field-grid field-row">
            <label>SMS provider<select value={providerSelection.sms} onChange={(event) => setProviderSelection((current) => ({ ...current, sms: event.target.value }))}><option value="twilio">Twilio</option></select></label>
            <label>Calendar provider<select value={providerSelection.calendar} onChange={(event) => setProviderSelection((current) => ({ ...current, calendar: event.target.value }))}><option value="google">Google Calendar</option></select></label>
            <label>Email provider<select value={providerSelection.email || ""} onChange={(event) => setProviderSelection((current) => ({ ...current, email: event.target.value || undefined }))}><option value="">Not configured</option><option value="sendgrid">SendGrid</option><option value="smtp">SMTP</option></select></label>
            <label>CRM provider<select defaultValue="" disabled><option value="">Not configured</option></select></label>
          </div>
          <p className="helper">Required adapters are SMS and calendar. Optional email and CRM adapters can be connected later. Credentials never pass through this form.</p>
        </section>

        <section className="card launch-card">
          <div className="section-heading"><span className="number">8</span><div><h3>Health check and launch</h3><p>Launch stays blocked until region policy, staff, hours, services, and required provider configuration are ready.</p></div></div>
          {!businessId ? <button className="primary-button" disabled={busy || templateErrors.length > 0 || !staff.some((person) => person.display_name.trim())} onClick={createAndCheck}>{busy ? "Saving and checking…" : "Create business and run health check"}</button> : <>
            <p className="business-id">Business ID <code>{businessId}</code></p>
            <button className="secondary-button" disabled={busy} onClick={runHealthCheck}>Refresh health check</button>
            {health && <div className="health-panel"><h4>{readyLabel}</h4>{Object.entries(health.checks).map(([name, passed]) => <div className="health-row" key={name}><span>{name.replaceAll("_", " ")}</span><strong className={passed ? "pass" : "fail"}>{passed ? "Ready" : "Needs setup"}</strong></div>)}
              {!!health.template_warnings?.length && <div className="health-blockers" role="status"><strong>Template warnings</strong><ul>{health.template_warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>}
              {!health.ready && <div className="health-blockers" role="status"><strong>Blocking checks</strong><ul>{Object.entries(health.checks).filter(([, passed]) => !passed).map(([name]) => <li key={name}>{name.replaceAll("_", " ")}</li>)}</ul></div>}
            </div>}
            <button className="primary-button" disabled={busy || !health?.ready} onClick={goLive}>Bring business online</button>
          </>}
          {notice && <p className="notice" role="status">{notice}</p>}
        </section>
      </>}
      </>}

    </main>
  );
}
