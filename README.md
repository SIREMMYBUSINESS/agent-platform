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

Copy `.env.example` to `.env` to customize local settings. Settings are validated when loaded; `.env.<ENVIRONMENT>` overrides `.env`, and process environment variables override both. For example, use `.env.production` for production-specific values. Supply `DATABASE_URL` and `REDIS_URL` from the deployment environment in production. API startup does not connect to PostgreSQL or Redis.

## Run locally

```bash
python -m uvicorn app.main:app --reload
```

The health endpoint is `GET http://127.0.0.1:8000/health`.

## Database schema

Production schema changes are managed with Alembic. Set `DATABASE_URL` to the target PostgreSQL database, then run migrations explicitly before starting inbound traffic:

```bash
python -m alembic -c alembic.ini upgrade head
```

The API does not create tables or run migrations during startup. Alembic revisions create tenant, business, contact, conversation, message, appointment, workflow history, and event log tables with tenant-scoped foreign keys. The current
`/inbound` endpoint is configured for one trusted tenant using
`INBOUND_TENANT_ID`; that tenant, business, and contact must be provisioned
before requests can be persisted. The request body's `tenant_id` is checked
against this trusted setting and is never used to select a tenant.

Provision the configured tenant, its business, and contacts through a trusted
administrative or deployment path before accepting inbound traffic. Inbound
requests only resolve existing records; missing records return 404 and are
never created from request data. Contact-to-business association is checked
within the configured tenant.

For a manual initial provision, run an audited SQL transaction through a
trusted database administration path and use the same tenant ID for
`INBOUND_TENANT_ID`:

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

Do not expose this provisioning transaction through the unauthenticated inbound
endpoint. Use established IDs in inbound requests; `tenant_id` must match the
configured trusted tenant.

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
for compatibility). It uses the configured `INBOUND_TENANT_ID`, an existing
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
the lead API uses it together with `INBOUND_TENANT_ID` as trusted ownership
scope. If it is unset, lead endpoints return `503` while the rest of the API
continues to start normally. Import requests contain a provisioned `contact_id`
and a tenant assertion; the assertion must match configuration. The request
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

Campaign endpoints use the same configured trusted tenant and
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
