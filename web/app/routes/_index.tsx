import { useMemo, useState } from "react";

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
  required_providers: string[];
  optional_providers: string[];
};
type Health = { ready: boolean; checks: Record<string, boolean>; provider_status: Record<string, string> };
type ProviderSelection = { sms: string; calendar: string; email?: string; crm?: string };

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");

export default function OnboardingRoute() {
  const [apiKey, setApiKey] = useState("");
  const [template, setTemplate] = useState<Template | null>(null);
  const [businessName, setBusinessName] = useState("");
  const [timezone, setTimezone] = useState("America/New_York");
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

  async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        ...init.headers,
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload as T;
  }

  async function loadTemplate() {
    setBusy(true);
    setNotice("");
    try {
      const choices = await api<{ items: Array<{ vertical_id: string }> }>("/verticals");
      const selected = choices.items.find((item) => item.vertical_id === "healthcare_dental");
      if (!selected) throw new Error("Dental clinic template is not available.");
      const data = await api<Template>(`/verticals/${selected.vertical_id}/template`);
      setTemplate(data);
      setQuestions(data.intake_questions);
      setHours(data.business_hours);
      setServices(data.services);
      setMessages(data.message_templates);
      setProviderSelection({ sms: "twilio", calendar: "google" });
      setNotice("Dental clinic defaults loaded. Review and customize your setup.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not load the template.");
    } finally {
      setBusy(false);
    }
  }

  async function createAndCheck() {
    if (!template || !businessName.trim() || !staff.some((person) => person.display_name.trim())) {
      setNotice("Enter a clinic name and at least one staff member.");
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
        ? "Setup is healthy. Review the checks, then bring the clinic online."
        : "Clinic saved. Complete the remaining checks before go-live.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not create the clinic.");
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
      setNotice("The dental clinic is live.");
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
        <div><p className="eyebrow">Agent Platform</p><h1>Clinic onboarding</h1></div>
        <span className="step-pill">About 5–10 minutes</span>
      </header>

      <section className="intro">
        <p className="eyebrow">Healthcare · Dental</p>
        <h2>Set up your digital front desk</h2>
        <p>Start with a dental clinic template, adjust it for your practice, and run setup checks before intake goes live.</p>
      </section>

      <section className="card credentials-card">
        <div className="section-heading"><span className="number">1</span><div><h3>Connect securely</h3><p>Use your existing onboarding API key for this session.</p></div></div>
        <label>API key
          <input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Paste the key from your administrator" />
        </label>
        <p className="helper">The key stays in this page's memory and is cleared when you close or reload it. Provider credentials belong in the protected secret manager, not here.</p>
        <button className="secondary-button" disabled={!apiKey || busy} onClick={loadTemplate}>{busy ? "Loading…" : "Load dental clinic template"}</button>
      </section>

      {template && <>
        <section className="card">
          <div className="section-heading"><span className="number">2</span><div><h3>Clinic details</h3><p>Business scope and local scheduling details.</p></div></div>
          <div className="field-grid">
            <label>Clinic name<input value={businessName} onChange={(event) => setBusinessName(event.target.value)} placeholder="Harbor Dental" /></label>
            <label>Business timezone<input value={timezone} onChange={(event) => setTimezone(event.target.value)} placeholder="America/New_York" /></label>
          </div>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">3</span><div><h3>Intake questions</h3><p>Questions are asked one at a time and saved with the conversation.</p></div></div>
          <div className="stack">
            {questions.map((question, index) => <label key={question.key}>{index + 1}. {question.key.replaceAll("_", " ")}
              <input value={question.prompt} onChange={(event) => updateQuestion(index, event.target.value)} />
            </label>)}
          </div>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">4</span><div><h3>Business hours</h3><p>Times use the clinic's configured timezone.</p></div></div>
          <div className="hours-list">
            {hours.map((hour, index) => <div className="hours-row" key={hour.weekday}>
              <label className="day-check"><input type="checkbox" checked={hour.active} onChange={(event) => updateHour(index, { active: event.target.checked })} />{DAYS[hour.weekday]}</label>
              <input aria-label={`${DAYS[hour.weekday]} opens at`} type="time" value={hour.opens_at} onChange={(event) => updateHour(index, { opens_at: event.target.value })} />
              <span>to</span>
              <input aria-label={`${DAYS[hour.weekday]} closes at`} type="time" value={hour.closes_at} onChange={(event) => updateHour(index, { closes_at: event.target.value })} />
            </div>)}
          </div>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">5</span><div><h3>Services and staff</h3><p>Service durations seed appointment scheduling. At least one staff member is required.</p></div></div>
          <div className="stack">
            {services.map((service, index) => <div className="field-grid service-row" key={index}>
              <label>Service<input value={service.name} onChange={(event) => updateService(index, { name: event.target.value })} /></label>
              <label>Duration in minutes<input type="number" min={10} max={240} value={service.duration_minutes} onChange={(event) => updateService(index, { duration_minutes: Number(event.target.value) })} /></label>
            </div>)}
            {staff.map((member, index) => <div className="field-grid" key={index}>
              <label>Staff name<input value={member.display_name} onChange={(event) => setStaff((current) => current.map((item, i) => i === index ? { ...item, display_name: event.target.value } : item))} placeholder="Dr. Rivera" /></label>
              <label>Staff email<input type="email" value={member.email} onChange={(event) => setStaff((current) => current.map((item, i) => i === index ? { ...item, email: event.target.value } : item))} placeholder="Optional" /></label>
            </div>)}
            <button className="text-button" onClick={() => setStaff((current) => [...current, { display_name: "", email: "" }])}>+ Add staff member</button>
          </div>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">6</span><div><h3>Patient messages</h3><p>Use variables such as <code>{"{{contact_name}}"}</code> and <code>{"{{clinic_name}}"}</code>.</p></div></div>
          <label>Greeting<textarea value={messages.greeting || ""} onChange={(event) => setMessages((current) => ({ ...current, greeting: event.target.value }))} rows={2} /></label>
          <label>Recall outreach<textarea value={messages.outreach || ""} onChange={(event) => setMessages((current) => ({ ...current, outreach: event.target.value }))} rows={3} /></label>
        </section>

        <section className="card">
          <div className="section-heading"><span className="number">7</span><div><h3>Provider connections</h3><p>Select adapters here; add credentials in your protected provider secret manager.</p></div></div>
          <div className="field-grid">
            <label>SMS provider<select value={providerSelection.sms} onChange={(event) => setProviderSelection((current) => ({ ...current, sms: event.target.value }))}><option value="twilio">Twilio</option></select></label>
            <label>Calendar provider<select value={providerSelection.calendar} onChange={(event) => setProviderSelection((current) => ({ ...current, calendar: event.target.value }))}><option value="google">Google Calendar</option></select></label>
            <label>Email provider<select value={providerSelection.email || ""} onChange={(event) => setProviderSelection((current) => ({ ...current, email: event.target.value || undefined }))}><option value="">Not configured</option><option value="sendgrid">SendGrid</option><option value="smtp">SMTP</option></select></label>
            <label>CRM provider<select value={providerSelection.crm || ""} onChange={(event) => setProviderSelection((current) => ({ ...current, crm: event.target.value || undefined }))}><option value="">Not configured</option><option value="hubspot">HubSpot</option><option value="salesforce">Salesforce</option></select></label>
          </div>
          <p className="helper">Required adapters are SMS and calendar. Optional email and CRM adapters can be connected later. Credentials never pass through this form.</p>
        </section>

        <section className="card launch-card">
          <div className="section-heading"><span className="number">8</span><div><h3>Health check and launch</h3><p>Launch stays blocked until region policy, staff, hours, services, and required provider configuration are ready.</p></div></div>
          {!businessId ? <button className="primary-button" disabled={busy} onClick={createAndCheck}>{busy ? "Saving and checking…" : "Create clinic and run health check"}</button> : <>
            <p className="business-id">Business ID <code>{businessId}</code></p>
            <button className="secondary-button" disabled={busy} onClick={runHealthCheck}>Refresh health check</button>
            {health && <div className="health-panel"><h4>{readyLabel}</h4>{Object.entries(health.checks).map(([name, passed]) => <div className="health-row" key={name}><span>{name.replaceAll("_", " ")}</span><strong className={passed ? "pass" : "fail"}>{passed ? "Ready" : "Needs setup"}</strong></div>)}</div>}
            <button className="primary-button" disabled={busy || !health?.ready} onClick={goLive}>Bring clinic online</button>
          </>}
          {notice && <p className="notice" role="status">{notice}</p>}
        </section>
      </>}
      <footer>Patient outreach remains consent-gated. Emergency symptoms are routed to clinic staff; this system does not provide diagnosis or treatment advice.</footer>
    </main>
  );
}
