# Agent Platform Skeleton

This project is the Phase 0 foundation for a receptionist and outreach agent platform.

It includes:

- Tenant and conversation models
- Workflow state machine
- Policy checker
- Event bus
- Inbound API
- Receptionist agent

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
```

On Windows, activate the environment with `.venv\Scripts\Activate.ps1`.

Copy `.env.example` to `.env` to customize local settings. Settings are validated when loaded; `.env.<ENVIRONMENT>` overrides `.env`, and process environment variables override both. For example, use `.env.production` for production-specific values. Production deployments must mount database and Redis URLs as protected secret files and set `DATABASE_URL_FILE` and `REDIS_URL_FILE`; production database connections require certificate-verified TLS. See the [production operations runbook](ops/runbooks/production-operations.md). API startup does not connect to PostgreSQL or Redis.

## Run locally

```bash
python -m uvicorn app.main:app --reload
```

The health endpoint is `GET http://127.0.0.1:8000/health`.

## Database schema

Production schema changes are managed with Alembic. Supply `DATABASE_URL` locally, or mount its protected secret file and set `DATABASE_URL_FILE` in production, then run migrations explicitly before starting inbound traffic:

```bash
python -m alembic -c alembic.ini upgrade head
```

The API does not create tables or run migrations during startup. Alembic revisions create tenant, business, contact, conversation, message, appointment, workflow history, and event log tables with tenant-scoped foreign keys. The `/inbound` endpoint resolves its tenant from a verified API key. That tenant,
business, and contact must be provisioned before requests can be persisted.
The request body's `tenant_id` is a compatibility assertion checked against
`principal.tenant_id`; it never selects the authorized tenant.

Provision the authenticated tenant, its business, and contacts through a trusted
administrative or deployment path before accepting inbound traffic. Inbound
requests only resolve existing records; missing records return 404 and are
never created from request data. Contact-to-business association is checked
within the configured tenant.

For a manual initial provision, run an audited SQL transaction through a
trusted database administration path, then provision an API key for that tenant:

```sql
BEGIN;
INSERT INTO tenants (id, name, region, default_timezone)
VALUES ('<tenant-id>', '<tenant-name>', 'US', 'UTC');
INSERT INTO businesses (id, tenant_id, name, vertical, region, default_language)
VALUES ('<business-id>', '<tenant-id>', '<business-name>', 'general', 'US', 'en');
INSERT INTO contacts (id, tenant_id, business_id, full_name, consent_status)
VALUES ('<contact-id>', '<tenant-id>', '<business-id>', '<contact-name>', 'unknown');
COMMIT;
```

Do not expose this provisioning transaction through the public inbound endpoint.
Use established IDs; `tenant_id` must match the authenticated principal.

### Session and transaction ownership

- The persistence `SessionFactory` owns the lazily created engine and centralized SQLModel sessionmaker. Its engine factory and URL provider can be injected in tests.
- Each Unit of Work creates and owns one session; no session is stored globally. The compatibility `get_session()` helper is a context manager that closes its session on exit.
- The application/service layer owns the Unit of Work scope. Entering the Unit of Work begins a transaction; normal exit commits, exceptional exit rolls back, and both paths close the session.
- Repositories use the Unit of Work session and must not commit or roll back independently. The Unit of Work owns commit and rollback.

## Run tests

```bash
python -m pytest -q -p no:cacheprovider
```

## PostgreSQL integration verification

The default test suite uses isolated SQLite databases and does not require a
PostgreSQL server. To run migration and inbound persistence verification
against a dedicated PostgreSQL test database, set `POSTGRES_TEST_DATABASE_URL`
to a database whose name ends in `_test`, then run:

```bash
python -m pytest -m postgres -q
```

The PostgreSQL tests create a temporary schema and remove it afterward. Do not
point this variable at a production or shared database.

## Receptionist intake and scheduling

The receptionist MVP handles the existing `/inbound` contract for `chat`,
`sms`, `voicemail`, and `phone` intake (the request's channel remains a string
for compatibility). It uses the authenticated principal tenant, an existing
business and contact in that tenant, and the contact's provisioned consent
status. Caller-provided consent, timezone, region, and routing metadata do not
control policy or routing. Provision the business timezone and, when useful,
the contact timezone as IANA names such as `America/Los_Angeles`.

For each business, provision one active `routing_rules` row per weekday:
`weekday` uses Monday `0` through Sunday `6`; `opens_at` and `closes_at` are
local wall-clock times in the business timezone. An overnight range is
supported when the closing time is earlier than the opening time. Set
`after_hours_action` to `voicemail` or `callback`; missing hours are treated as
unsafe configuration and sent to human escalation rather than guessed.

Scheduling uses provisioned active `services`, active `staff`, and future
`appointment_slots` that reference the same tenant and business. Intake
reserves one available slot and creates a proposed appointment. A subsequent
affirmative inbound message confirms the latest proposal for that contact and
marks the slot booked. Maintain slot availability through a trusted admin
path; inbound data cannot create or change staff, services, or slots.

Urgent phrases and requests the service cannot safely handle create an open
`escalation_tickets` row and a `human.escalation.alert` event in the same
transaction. Appointment confirmations also persist a reminder-prepared
workflow state and event. This phase does not deliver SMS, voicemail, reminder,
or staff alerts through external providers; outbound messages and alert intent
are recorded for a later channel adapter. All conversation, message, workflow,
appointment, escalation, and event writes share the Unit of Work transaction.

## Phase 2A lead import

Set `LEAD_BUSINESS_ID` to the provisioned business assigned to this deployment;
the lead API uses it together with `principal.tenant_id` as trusted ownership
scope. If it is unset, lead endpoints return `503` while the rest of the API
continues to start normally. Import requests contain a provisioned `contact_id`
and a tenant assertion; the assertion must match the authenticated principal. The request
does not accept a business ID, and the contact must belong to the configured
business. Lead ownership is always derived from these trusted settings and
database records.

`POST /leads/import` creates a lead source when needed and persists lead,
optional enrichment, score, lifecycle transitions, and events in one Unit of
Work transaction. Enrichment runs through an adapter; the default adapter is a
no-op. Provider failures are captured as an audit event and do not abort the
import. Scores are deterministic (fit 40%, urgency 30%, intent 30%); priority
scores of 65 or higher enter `qualified`. `GET /leads` and `GET /leads/{id}`
are restricted to the same configured tenant/business. No outbound contact is
sent in this phase.

Lead schema changes are in Alembic revision `0004_lead_import`; apply with the
same migration command above before using lead endpoints.

## Phase 2B campaign orchestration

Campaign endpoints use the authenticated principal tenant and configured
`LEAD_BUSINESS_ID`; campaign requests cannot choose a business. `POST
/campaigns` creates a draft campaign with an audience filter and an ordered,
data-driven sequence. Templates accept `subject` and `body` strings and the
supported placeholders `{{contact_name}}`, `{{business_name}}`, and
`{{company_name}}`. Step delays are seconds from enrollment for the first step
and from the prior step run for later steps.

`POST /campaigns/{id}/enroll` accepts leads only when they belong to the
configured tenant/business, are qualified, meet the minimum priority score,
and have granted consent for the campaign channel. Duplicate enrollment is
idempotent. `POST /campaigns/{id}/trigger` activates a draft campaign and
processes currently due steps. It does not contact an external provider. For
this orchestration-only phase, a `sent` step means the rendered message was
accepted and persisted by the local campaign engine; `delivery_mode` is marked
`simulated`. Eligibility failures create a skipped step record. Campaign,
enrollment, step, state, and completion events are persisted transactionally.

Campaign schema changes are in Alembic revision
`0005_campaign_orchestration`; migrate to `head` before using campaign routes.

## Phase 2C replies, opt-outs, and campaign analytics

`POST /campaigns/{id}/replies` accepts a tenant assertion, enrollment ID, reply
text, and an optional external message ID for idempotency. The campaign channel,
lead, contact, tenant, and business are resolved from persisted campaign records
and trusted configuration. Reply classification is deterministic and stores
only the classification and processing metadata; the reply text is not retained.
Classifications are `positive`, `negative`, `opt_out`, `book_appointment`,
`callback`, and `unknown`.

An opt-out creates or updates a channel-specific consent record to `revoked`,
cancels that enrollment, moves the lead to `opted_out`, and records campaign and
lead events in the same transaction. Future campaign eligibility checks honor
the revocation. The existing contact-level consent remains a prerequisite, and
region/channel policy checks still run before simulated campaign delivery.
No email, SMS, or messaging provider is called by this phase.

`GET /campaigns/{id}/analytics` aggregates sent step records, `step.opened`
events, persisted classified replies, and `campaign.appointment.booked` events.
Positive reply rate is a fraction from 0 to 1 and treats positive, appointment,
and callback classifications as positive intent. Booking request classification
does not itself book an appointment. Open and booked counts depend on those
events being written by a trusted integration/application flow.

The schema is in Alembic revision `0006_reply_optout_analytics`; apply migrations
through `head` before using the new routes.

## Phase 3 geo-aware compliance policy

Compliance decisions use the provisioned business country/region, tenant
configuration, policy profile, country policy, consent records, and suppression
rules. Request `region`, metadata, and `consent_status` fields are never used as
authority. Inbound policy is evaluated before intent classification and
routing. Campaign enrollment and each simulated campaign step recheck policy,
so a later revocation or suppression blocks subsequent work.

Country rules are stored in `country_policies` and seeded by migration
`0007_geo_policy_engine` for US, CA, EU, and an Africa safe-mode profile.
Country-specific African policy must be provisioned by a trusted operator using
the country's code before normal processing is allowed. When a specific
`Business.country_code` is set, that exact country policy must exist; the engine
does not silently apply a broader region's rules to an unknown country.
Unrecognized regions fail closed. `policy_profiles.custom_rules` can add restrictions; an explicit
override is allowed only for a configured business-level custom restriction,
with a configured override policy, actor, and reason. Consent revocations,
expired consent, and suppression rules cannot be overridden.

`consent_records` preserves channel-specific grant, revocation, expiration, and
unknown lifecycle entries. The legacy contact-wide `consent_status=granted`
does not satisfy a channel-specific consent gate; only a trusted channel
consent record does. Legacy denied/revoked contact state remains blocking.
`suppression_rules` target provisioned lead or
contact IDs and can apply to one channel or all channels. `policy_decision_logs`
records each allow/block decision and any override attempt without storing raw
reply text or contact details. There is no public endpoint for policy,
consent, suppression, or override administration; those records must be
provisioned through a trusted administrative path.


## Phase 1D API-key authentication

This is an intentional API contract change: `/inbound`, `/leads`, and
`/campaigns` require `Authorization: Bearer <api-key>`. `/health` and the API
documentation remain public. There is no fallback to `INBOUND_TENANT_ID` for
HTTP authentication. Apply Alembic through `0008_api_key_auth` before using
protected endpoints; startup still does not connect to the database.

Authentication follows header extraction, full-key verification, provisioned
tenant lookup, active grant validation, and `AuthenticatedPrincipal` creation.
Service factories derive their tenant from `principal.tenant_id`. Caller IDs
select resources only within that scope; a mismatched body `tenant_id` returns
403, and unavailable/cross-tenant resources return 404. Missing, malformed,
unknown, revoked, or expired keys return 401. Missing/disabled/expired grants
and insufficient roles return 403. No public endpoint provisions credentials,
tenants, businesses, or contacts.

Roles are explicit tenant-wide grants (no implicit admin or writer-to-reader
inheritance):

| Operation | Required role |
| --- | --- |
| Inbound intake | `inbound_writer` |
| Lead import | `lead_writer` |
| Lead reads | `lead_reader` |
| Campaign creation, enrollment, triggering, replies | `campaign_writer` |
| Campaign details and analytics | `campaign_reader` |

Lead/campaign operations retain the configured `LEAD_BUSINESS_ID` restriction,
scoped to the principal tenant. Per-business grants and service methods taking
a principal directly remain future authorization work; do not issue a
tenant-wide role to an identity intended to access only one business.

Credential provisioning is a trusted administrative operation:

1. Generate a random key with `app.auth.api_keys.generate_api_key()` (256 bits).
2. In an administrative transaction, insert `ApiKey` for an existing tenant,
   storing only `hash_api_key(key)`, its unique ID, UTC issue/expiry timestamps,
   and `revoked=False`. The key ID is the integration principal ID.
3. Insert `ApiKeyGrant` for that same key ID and tenant with the minimum roles,
   `enabled=True`, and an optional UTC expiration. Persist both atomically.
4. Deliver the raw key once through a secure channel. Never put it in logs,
   source control, URLs, or request bodies. Use HTTPS outside local development.

SHA-256 is used for generated high-entropy keys, not human-chosen passwords.
Key/grant validity is checked on each request without a principal cache. To
rotate, provision a replacement and revoke the old key after clients switch;
revocation does not cancel an already executing request. Grant expiry also
limits principal expiry. Database/repository failures fail closed.

Existing behavior tests inject a principal explicitly. `test_api_key_auth.py`
exercises actual verification, persisted grants, dependency resolution, role
checks, and protected routes with isolated SQLite storage. Live PostgreSQL
verification still requires `POSTGRES_TEST_DATABASE_URL`.


## Phase 2A conversation management

Apply migration `0009_conversation_management` before using this version. It
adds `status` and `version` to conversations and a current-assignment table.
Existing conversations start at `open`, version `0`; existing workflow states
are preserved. Management lifecycle and receptionist execution state are
separate. This phase does not resume messages into an existing conversation;
`/inbound` retains its existing create-per-request behavior.

The authenticated principal is passed directly into `ConversationService`.
The service checks validity and roles before opening its Unit of Work, and
uses only `principal.tenant_id` for scope. Requests cannot specify tenant or
business ownership. Creation resolves the business from an already-provisioned
contact. Unknown and cross-tenant resources both produce 403 on these new
routes; older endpoint response contracts are unchanged.

| Endpoint | Required role | Input |
| --- | --- | --- |
| `POST /conversations` | `conversation_writer` | `contact_id`, `channel` |
| `GET /conversations` | `conversation_reader` | `offset`, `limit` (1�100) |
| `GET /conversations/{id}` | `conversation_reader` | Conversation ID |
| `PATCH /conversations/{id}/status` | `conversation_writer` | `status`, `expected_version` |
| `PUT /conversations/{id}/assignment` | `conversation_writer` | `staff_id` (null to unassign), `expected_version` |

Provision these roles explicitly; writer does not implicitly grant reader.
Roles grant access within the principal tenant, not across tenants. Unexpected
body fields are rejected. Tenant query parameters cannot change authorization.

Allowed lifecycle transitions are `open -> pending/closed`,
`pending -> open/closed`, and `closed -> open`. Closed conversations must be
reopened before assignment changes. Assignment requires active staff in the
same tenant and business, with database foreign keys enforcing the ownership
relationships. One assignment row stores the current assignment; event logs
record changes and the authenticated actor ID.

Responses include `version`. Mutating requests must supply that version;
stale changes return 409 using an atomic version check. Repeating an already
satisfied status/assignment returns the current response without another event
or version increment. This is state-based retry handling, not an idempotency
key for conversation creation. Each successful create request creates a new
conversation.

Conversation updates, assignment updates, and audit events share one Unit of
Work transaction. An event persistence failure rolls back the entire operation.
Tests cover these guarantees with SQLite; live PostgreSQL migration and
concurrency verification remains part of the database integration gate.


## Phase 2B receptionist workflows

Apply migration `0010_receptionist_workflows` before using workflow actions.
The authenticated `workflow_executor` role is required for workflow execution.
`POST /conversations/{id}/workflows/{action}` supports `assign`, `unassign`,
`escalate`, `reopen`, `close`, and `schedule_follow_up`. Every request supplies
an idempotency key; action fields are explicit and unknown body fields are
rejected. Follow-up times must include a timezone and be in the future.

Executions, retry attempts, transition states, follow-up tasks, and action audit
events are persisted under the principal tenant and conversation business.
Idempotency keys are unique per tenant and bound to the workflow, conversation,
and canonical action input. A failed action rolls back its business writes and
stores a retryable failure record; retrying the same key and input runs the
action again. Reusing a key for different input is rejected.

Business-hours evaluation reads active routing rules for the principal tenant
and business and uses the business timezone. `next_business_time` returns the
next valid opening or `None` when no opening exists in the search window.
Escalation evaluation is deterministic: failed delivery takes precedence,
followed by unassigned and response-timeout rules. Run the escalation sweep
from a trusted worker principal with access to the target business. Thresholds
are explicit configuration values on `EscalationRules`.

Workflow transaction and timezone behavior are covered with SQLite tests.
Live PostgreSQL migration and concurrency verification remains part of the
database integration gate.


## Phase 2C outreach execution

The contact-based outreach records are separate from the existing lead-sequence
step runs. Apply migration `0011_outreach_execution` before using the new
endpoints. Campaign creation remains draft; activate before selecting recipients
or scheduling messages. Campaign status endpoints support activate, pause, and
archive; archived campaigns are terminal.

`POST /campaigns/{id}/recipients/select` returns tenant/business-scoped contact
IDs and exclusion counts. Selection applies the existing regional consent and
suppression policy, and already selected campaign contacts are excluded.
`POST /campaigns/{id}/messages` schedules an idempotent message with an
explicit contact, content, and timezone-aware scheduled time. Execute, retry,
delivered, and failed actions are available under `/campaigns/messages/{id}`.

Each send record stores scheduled, sent, delivered, and failed timestamps,
provider-safe failure reason, attempt count, and a stable idempotency key.
Campaign audit events record the authenticated actor ID and message ID, but do
not copy message content into logs. Rate limits default to 100 tenant and 25
campaign messages per rolling hour, with four total provider attempts per
message. Inject a channel provider adapter into `OutreachExecutionService`;
without one, execution records a retryable `sender_not_configured` failure.
The provider adapter must honor the idempotency key to protect retries from
duplicate delivery.

## Phase 4 queue and autonomy runtime

Apply `0014_durable_task_queue` before using queued intake. Queue records are the durable outbox and source of retry/dead-letter status; Redis carries task IDs only. PostgreSQL remains authoritative for task scope, idempotency, workflow state, and events. Configure Redis through `REDIS_URL`, then run:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Queued intake is additive; the existing synchronous `POST /inbound` contract is unchanged. Authenticate with an `inbound_writer` key and provide an idempotency key:

```http
POST /inbound/queue
Authorization: Bearer <provisioned-app-key>
Idempotency-Key: inbound-event-2026-10-06-001
Content-Type: application/json

{
  "tenant_id": "tenant-from-provisioning",
  "business_id": "business-from-provisioning",
  "contact_id": "contact-from-provisioning",
  "channel": "sms",
  "text": "I need to schedule an appointment"
}
```

The authenticated principal supplies tenant authority. The endpoint verifies that the business and contact belong to that tenant and business, then persists the task before returning `202 Accepted`. Repeating the same key and payload returns the same queue task. Reusing a key for different work returns `409`. The payload excludes client region, consent, and routing metadata; inbound workers reload trusted records and call `IntakeService`.

Celery routes tasks to five bounded domain queues: `agent.inbound`, `agent.outreach`, `agent.scheduling`, `agent.escalation`, and `agent.analytics`. Run one worker pool per queue and one Beat process to dispatch due outbox rows and time-based tasks. Example production starting points are:

```sh
celery -A app.queue.celery:celery_app worker -Q agent.inbound --concurrency=4
celery -A app.queue.celery:celery_app worker -Q agent.outreach --concurrency=8
celery -A app.queue.celery:celery_app worker -Q agent.scheduling --concurrency=2
celery -A app.queue.celery:celery_app worker -Q agent.escalation --concurrency=2
celery -A app.queue.celery:celery_app worker -Q agent.analytics --concurrency=2
celery -A app.queue.celery:celery_app beat
```

These concurrency values are initial capacity assumptions, not measured throughput guarantees. Benchmark with the deployment database, broker, providers, and workload before increasing them. Windows development should use Celery's `--pool=solo`; separate concurrent pools are intended for a Linux worker deployment.

Internal producers use `app.tasks.submission.enqueue_task` with a validated `QueueTaskSubmission`; domain task names include `outreach.execute_send`, `scheduling.confirm_appointment`, `escalation.sweep`, and `analytics.refresh_campaign`. The Celery task contains only the durable task ID. Workers claim it in PostgreSQL, enforce a lease, call a registered service handler, and persist success or a sanitized failure code. Retry delay starts at five seconds, doubles per attempt, and caps at five minutes; five attempts are the default. Exhausted tasks enter `dead_letter`. A trusted `workflow_executor` can redrive a dead-letter row in its tenant/business scope. Duplicate broker deliveries are safe because workers claim durable state and each command retains its idempotency key.

Event replay reads the persisted event by tenant, business, and event ID, then queues only that reference. Subscribers register explicitly with `app.workers.event_replay.register_event_subscriber`; subscribers receive the durable event and replay task idempotency key. Worker business rules remain in application services and policy checks; queue code handles delivery and task lifecycle only. Existing conversation workflow history and event tables remain the audit record.

The worker bootstrap hook is optional. Set `WORKER_BOOTSTRAP_MODULE` to an importable module defining `configure_worker_handlers(registry)` to replace a built-in handler with provider-backed deployment wiring. Never place provider credentials in task payloads. The built-in outreach worker has no sender until deployment configures one; existing outreach policy and rate limits still run inside `OutreachExecutionService`.

`NextBestActionService` combines scored lead signals with a persisted policy decision, contactability, business hours, appointment availability, and campaign capacity. `SequenceExperimentService` assigns stable weighted A/B variants per lead and reports delivered-to-conversion rates. Scheduling slots can be ranked by local time, allowed weekdays, and an optional planning horizon. These decision helpers do not replace consent, regional policy, or suppression checks.

Queue, retry, redrive, API acceptance, dynamic routing, experiment assignment, and scheduling ranking have SQLite tests. The queue does not claim live Redis, live provider delivery, or production worker throughput; those require deployment readiness checks.

Campaign creation can include up to four named `sequence_variants` alongside the existing `steps` control sequence. Enrollment deterministically assigns each tenant/business/campaign/lead to one sequence and records the arm in the campaign event. The analytics response includes per-arm enrollment count, sent count, conversion count, and conversion rate; the existing campaign-level metrics remain available.

## Provider integrations (Phase 5)

Provider credentials belong in a protected, read-only secret volume, never in application settings, `.env`, task payloads, or source control. Set `PROVIDER_SECRETS_DIR` to the mount and `WORKER_BOOTSTRAP_MODULE` to `app.integrations.bootstrap_example`. The bootstrap builds tenant-scoped SMS, email, voice, calendar, and CRM adapters. Secret files use the SHA-256 filename of `tenant_id + NUL + business_id` and contain provider selectors (`sms_provider`, `email_provider`, `voice_provider`, `calendar_provider`, `crm_provider`) plus that scope's credentials and configuration.

Supported adapters include Twilio SMS and voice, SMTP email, Google Calendar, and HubSpot or Salesforce CRM. Configure Twilio inbound SMS to `POST /webhooks/sms?tenant_id=...&business_id=...` and set the exact public URL in `sms_inbound_webhook_url`. Configure the Twilio call callback at `/webhooks/call` and set `call_webhook_url`. Email and CRM callbacks use `/webhooks/email` and `/webhooks/crm`, scoped with those query parameters and signed with the configured callback secret. Verified webhook events are idempotently stored in `provider_webhook_events`; provider attempt metadata is stored in `provider_interaction_logs` without credentials or message content. Inbound SMS must match a contact provisioned under that tenant and business and is sent through the existing intake queue and policy checks. Inbound email replies use `POST /webhooks/email/inbound?tenant_id=...&business_id=...`; configure `inbound_email_address` and a high-entropy `email_inbound_webhook_secret` in that business secret file. The trusted mail gateway sends JSON `{ "from": "customer@example.com", "to": "inbox@example.com", "message_id": "provider-id", "text": "reply" }` with `X-Provider-Signature: sha256=<HMAC-SHA256 of the exact raw body>`. The endpoint matches the provisioned email, persists webhook idempotency, applies opt-outs immediately, and queues other replies for intake. For chat that should execute asynchronously, use the authenticated `POST /inbound/queue`; `/inbound` remains the backward-compatible synchronous API.

Before production, configure sandbox credentials and callback URLs in the secret manager, apply migrations through `0015_provider_webhook_events`, run provider tests, then exercise a controlled sandbox message and callback. SMTP uses a stable Message-ID and idempotency header, but SMTP servers do not universally guarantee deduplication after ambiguous connection failures; delivery is at-least-once. Twilio sends reuse the durable outreach message idempotency key.

Provider secret file examples use these keys (values are supplied by the secret manager):

- Twilio SMS/voice: `sms_provider=twilio`, `voice_provider=twilio`, `account_sid`, `auth_token`, `from_sms` or `from_call`, `status_callback_base_url`, `sms_inbound_webhook_url`, `call_webhook_url`, `voicemail_action_url`, and `recording_enabled=false` unless recording is approved by policy.
- SendGrid: `email_provider=sendgrid`, `api_key`, `from_address`, and `email_webhook_public_key` copied from Signed Event Webhook configuration. Its open, click, bounce, and complaint events are signature checked and durably recorded.
- SMTP alternative: `email_provider=smtp`, `host`, `port`, `from_address`, optional `username` and `password`, plus `starttls` or `implicit_tls`.
- Google Calendar: `calendar_provider=google`, `access_token`, and `calendar_id`. The adapter lists events in a time range and supports event creation and cancellation; pass attendee addresses in event data to deliver invites.
- HubSpot/Salesforce: `crm_provider=hubspot` with `access_token`, or `crm_provider=salesforce` with `instance_url`, `access_token`, `api_version`, and configured external ID field names. CRM sync operations use the existing durable sync service; CRM event callbacks are authenticated and stored for reconciliation.

The application has no provider credentials in configuration. Deployments must provision per-business secret files, migrate the database, configure the worker bootstrap, register provider callback URLs, and run sandbox exercises. No live provider credentials or sandbox tenant were available for this verification run.

Signature specifics: Twilio callbacks use `X-Twilio-Signature` and the tenant-scoped auth token. SendGrid uses its signed Event Webhook ECDSA public key in `email_webhook_public_key` and the timestamp/signature headers. The CRM webhook endpoint currently expects an `X-Provider-Signature` HMAC-SHA256 using `crm_webhook_secret`; configure a trusted gateway to validate native HubSpot/Salesforce signatures and forward only verified requests when those native formats differ.

## End-to-end workflows (Phase 6)

Appointment confirmation is an explicit durable transition. An authorized `workflow_executor` can retrieve a tenant-scoped appointment with `GET /appointments/{id}`, confirm it with `POST /workflows/confirm-appointment` and body `{"appointment_id":"..."}`, or cancel it with `POST /appointments/{id}/cancel` and body `{"reason":"Customer request"}`. A contact can use the same endpoint with the appointment action `token` in the body; the capability is matched to the path ID server-side and determines the trusted tenant. Confirmation/cancellation changes the local appointment and reserves/releases its local slot in a transaction, writes an event, and creates an idempotent scheduling queue task in that same transaction. Calendar create/cancel operations run from `scheduling.calendar_sync`; configure a tenant-scoped `calendar_provider` in the worker bootstrap. Calendar create requests use the appointment ID as their stable idempotency key. A provider outage leaves the appointment durable while the queue retries and ultimately dead-letters exhausted work.

`GET /campaigns/{id}/funnel` returns the same persisted campaign conversion counts and variant metrics as `/analytics`. Human staff can resolve an escalation with `POST /escalations/{id}/resolve` and `{"resolution":"..."}`; this writes the status and `escalation.resolved` event under the authenticated tenant.

Migration `0016_phase6_workflow_fields` adds appointment calendar/CRM/correlation references, contact channel/language preferences, and escalation resolution details. Apply it with `alembic upgrade head` before deploying this release.

Set `WORKFLOW_DRY_RUN=true` on workers to persist delivery intent and report provider-bound message, calendar, CRM, and staff notification tasks as planned without calling those providers. The database workflow still advances, so use an isolated test tenant and inspect durable events, messages, appointments, and provider interaction logs. This is a worker-wide setting rather than a request-level switch.

Vertical flow configuration remains business-owned: healthcare can use intake, proposed appointment, confirmed calendar event, then follow-up; legal can intake a matter, route document questions to the configured knowledge service, then schedule a consultation; real estate can qualify a lead, propose a viewing, then trigger follow-up. These domain templates require business policy, provider secrets, staff, services, appointment slots, consent records, and contact preferences before activation. Calendar availability is checked when proposing and confirming; provider-side create failures retry and eventually dead-letter for staff review.

Troubleshooting: missing calendar configuration is retried then dead-lettered as `calendar_provider_not_configured`; inspect the scoped queue task and configure the tenant/business adapter before redrive. CRM sync remains optional and should be reconciled from its durable sync records; CRM outages do not invalidate an appointment. A cancelled or unconsented contact is stopped by the policy service before any future outbound delivery. Provider-backed delivery and calendar/CRM sync require configured tenant/business secrets and sandbox verification.

## Phase 6 runtime notes

This section supersedes the older receptionist workflow notes above. Outbound SMS/email responses are policy checked, recorded on the message, and queued to the outreach worker; chat responses are returned by the synchronous chat endpoint. Confirmed appointments enqueue calendar sync, 24-hour/1-hour reminders when still upcoming, completion follow-up, and optional CRM activity records. Calendar availability is checked when proposing and again when confirming; conflicts release the prior slot and propose another available slot. Outreach replies classified as appointment requests enqueue the same scheduling proposal service and carry campaign/enrollment links through booking and completion analytics.

Apply migrations through `0024_outreach_dry_run_status`. Set `WORKFLOW_DRY_RUN=true` on workers to record outbound SMS/email, calendar, CRM, and staff notification plans without provider calls. Legacy campaign message execution also runs from the durable outreach queue; dry-run marks the message `dry_run` and does not call the provider. Escalation tickets are durable and available in the authenticated inbox; configure staff email and a knowledge adapter for those optional integrations.

### Phase 6 workflow updates

General customer questions are classified separately from scheduling and business-hours requests. `FAQService` is the knowledge boundary: until a trusted tenant/business knowledge adapter is configured, it returns a transparent “team will follow up” fallback and records `faq.answer.generated`; it does not invent factual answers. The resulting message still passes the outbound policy gate.

Human escalation tickets appear in the authenticated dashboard inbox at `GET /escalations?status=open` (filter by `in_progress`, `resolved`, or `escalated_to_human`; paginate with `offset` and `limit`). The endpoint is tenant-scoped and requires `workflow_executor`. Staff can resolve through `POST /escalations/{id}/resolve` with `resolution`; to send an approved answer to the customer, also provide `response_to_contact`. That reply is durably queued and rechecked against current consent and suppression before provider delivery. The `human.escalation.alert` event is the durable alert/inbox signal; an external Slack/email notifier still requires deployment wiring.

A negative outreach reply cancels the active enrollment, clears its next-step timer, closes the lead, and records lead and campaign events. Positive replies retain the scheduled sequence; booking replies use the appointment proposal workflow. Opt-outs revoke channel consent and cancel enrollment atomically.

### Phase 6 vertical examples

All examples use previously provisioned tenant, business, contact, channel consent, and provider configuration. Never include caller-supplied region or consent metadata; policy reads trusted persisted configuration. Replace the IDs below with provisioned values.

Healthcare receptionist intake:

```json
{
  "tenant_id": "tenant-123",
  "business_id": "clinic-456",
  "contact_id": "contact-789",
  "channel": "sms",
  "text": "I need an appointment for a consultation"
}
```

Post this to `/inbound`. Configure clinic services, active staff and slots, Google Calendar, approved SMS delivery, and contact SMS consent. A confirmed booking queues calendar sync, reminders, CRM activity when configured, and post-appointment follow-up.

Legal intake document request:

```json
{
  "tenant_id": "tenant-123",
  "business_id": "firm-456",
  "contact_id": "contact-789",
  "channel": "chat",
  "text": "What documents should I bring to my consultation?"
}
```

Until a trusted knowledge adapter is supplied, the FAQ response says that staff will follow up. Configure legal intake guidance in the business knowledge adapter before relying on automated factual answers. Appointment requests use the same configured service, staff, slot, and policy workflow.

Real estate outreach campaign:

```json
{
  "tenant_id": "tenant-123",
  "name": "Viewing follow-up",
  "channel": "email",
  "audience_filters": {"minimum_priority_score": 65},
  "steps": [
    {"delay_seconds": 0, "template": {"subject": "Property viewing", "body": "Would you like to schedule a viewing?"}},
    {"delay_seconds": 86400, "template": {"subject": "Viewing follow-up", "body": "Would another viewing time work for you?"}}
  ]
}
```

Create the campaign in the tenant-scoped campaigns API, enroll eligible leads with recorded consent, and trigger the campaign. Configure contact email preferences, provider credentials, property context in the approved enrichment/template path, and calendar slots for viewings. Positive booking replies create an outreach-linked appointment proposal; negative replies close the lead and stop its sequence; opt-outs revoke consent and stop future delivery.

To dry-run outreach delivery, set `WORKFLOW_DRY_RUN=true` for workers and inspect message `delivery_status` and workflow events; dry-run does not validate provider delivery. Keep Calendar/CRM adapters on sandbox configuration when executing appointment workflows. For a blocked send, inspect the policy decision and contact consent/suppression records. For calendar conflicts, the workflow releases the old proposal and generates another; for CRM outages, inspect the independent `crm.sync_activity` queue task and redrive after configuration is repaired.

Appointment proposals persist the offered slot IDs (`proposal_slot_ids`) in their appointment record and present the available choices as numbered local times. SMS/chat replies accept `1`, `2`, `3`, `option 2`, and ordinal forms; the worker rechecks and atomically reserves the exact offered slot. If another booking claimed it or Calendar now reports a conflict, the proposal is released and alternate choices are sent. Proposals also include a `PUBLIC_APP_URL/appointments/action?token=...` link. Only the SHA-256 token hash is stored on the appointment. A customer web client submits `{ "token": "...", "slot_number": 1 }` to `POST /workflows/confirm-appointment`, or `{ "token": "...", "reason": "..." }` to `POST /contact/appointments/cancel`; these requests do not accept tenant, business, contact, or appointment IDs. Confirmation reuses calendar conflict checks and queues calendar, CRM, and reminder work. Set `PUBLIC_APP_URL` to the deployed customer web application origin; this repository provides the API contract, not a customer web page. Apply migrations through `0024_outreach_dry_run_status` before deploying this behavior.

Business-wide lead conversion is available at `GET /businesses/{business_id}/funnel` for principals with `campaign_reader` or `workflow_executor`. The endpoint verifies the business under the authenticated tenant and returns lead, contacted, replied, booked, completed, and converted counts. Campaign funnel analytics remain available at `GET /campaigns/{id}/funnel` and expose campaign-specific delivery, reply, booking, completion, and variant counts.

### Staff escalation notifications and FAQ configuration

Provision each active `staff` row with its email address to receive urgent escalation alerts. `EscalationService` writes the ticket, alert event, and idempotent `escalation.notify_staff` task in one transaction. Workers send a PII-minimized notice through the tenant/business email adapter; exhausted provider failures follow the normal retry/DLQ path. Tickets remain available in `GET /escalations` if no staff email is configured or notification delivery fails. The notification task key is stable per ticket and staff member.

To enable exact FAQ responses, configure the worker bootstrap and include a business-scoped `faq_entries_json` value in its protected tenant/business configuration. It is a JSON array, for example `[{"question":"What are your hours?","answer":"Monday through Friday, 9 AM to 5 PM."},{"keywords":["parking"],"answer":"Street parking is available."}]`. Exact-question and unique keyword matches return the configured answer. Missing or ambiguous matches use the human follow-up fallback; malformed configuration fails the task visibly and retries before DLQ. Apply migrations through `0024_outreach_dry_run_status` before deploying the current Phase 6 schema.
