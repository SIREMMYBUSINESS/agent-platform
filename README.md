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
