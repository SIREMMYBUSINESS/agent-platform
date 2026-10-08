# Deploying a worker on Railway

The worker image is `Dockerfile.worker`; it runs Celery without exposing an HTTP
port. Keep the API, worker, and Celery Beat as separate Railway services. Do
not assign a public domain to the worker or Beat service.

## Deploying the Remix UI

Create a separate Railway service from the same repository and set its root
directory to `web`. Use `npm install && npm run build` as the build command and
`npm run start` as the start command. Generate a public domain for the UI, then
set `VITE_PLATFORM_API_URL=https://<api-domain>` on the UI service and
`CORS_ALLOWED_ORIGINS=["https://<ui-domain>"]` on the API service. The CORS
value is a JSON array of exact origins, with no trailing slash or wildcard.
Redeploy both services after setting the variables; Vite embeds
`VITE_PLATFORM_API_URL` during the UI build. The onboarding page takes an
authorized API key in the browser; do not put that key in a Railway variable
or any `VITE_` variable.

## Railway worker project setup

1. Connect the committed repository as a new Railway service named
   `agent-worker-inbound`. Set the service's `RAILWAY_DOCKERFILE_PATH`
   variable to `/Dockerfile.worker`. Its image command starts the worker; the
   start command can also be set to
   `python -m app.workers.railway_entry worker` in the service settings.
2. Create four additional worker services from the same repository and image.
   Set each service's `WORKER_DOMAINS` to one of `outreach`, `scheduling`,
   `escalation`, or `analytics`. Leave `agent-worker-inbound` at
   `WORKER_DOMAINS=inbound`. Set `WORKER_CONCURRENCY` per service after
   benchmarking; the defaults come from `app.queue.routing`.
3. Create one Beat service from the same repository and Dockerfile, with start
   command `python -m app.workers.railway_entry beat`. It dispatches the
   durable database outbox and periodic optimization tasks. The inbound worker
   consumes `agent.control` tasks. Do not scale Beat to multiple replicas.
4. Give every service private reference variables for the same PostgreSQL and
   Redis endpoints. The worker launcher moves Railway's injected `DATABASE_URL`
   and `REDIS_URL` values into owner-only read-only temporary files before
   loading production settings. It removes the raw URL values from the worker
   process environment. No URL is printed.
5. Set these variables on all worker and Beat services:

   - `ENVIRONMENT=production`
   - `DATABASE_URL` to a TLS verified PostgreSQL URL using the `psycopg` SQLAlchemy driver, for example `postgresql+psycopg://...?...sslmode=verify-full`
   - `REDIS_URL` to a TLS Redis URL (`rediss://...`)
   - `DEPLOYMENT_REGION_ID`, `DATABASE_REGION_ID`, and `REDIS_REGION_ID` to the same supported application region ID
   - `WORKER_DOMAINS` for each domain worker; `WORKER_CONCURRENCY` if a non-default capacity is needed
   - `WORKER_BOOTSTRAP_MODULE` and `PROVIDER_SECRETS_DIR` only when provider-backed handlers need the deployment's protected secret configuration

For outbound Twilio voice, set `WORKER_BOOTSTRAP_MODULE=app.integrations.bootstrap_example`
and `PROVIDER_SECRETS_DIR` on the outreach worker. Railway attaches a volume to
one service at a time and mounts it read/write as root, so create a separate
protected volume and copy of the scoped secret file for the API and outreach
worker. Set each service's `PROVIDER_SECRETS_DIR` to its volume mount path; the
API serves signed TwiML for Twilio. Railway volumes are writable, so set the
JSON file permissions to `0440` and ensure the worker process can read it (the
worker image runs as UID/GID `10001`; use root ownership with group `10001` or
an equivalent protected permission setup). The resolver rejects writable
secret files. Configure the
business secret file with `voice_provider=twilio`, `account_sid`, `auth_token`,
`from_call` (the Twilio voice-enabled number in E.164),
`outbound_call_twiml_url` (exact public `/webhooks/call/twiml` URL with tenant
and business query values), `outbound_call_message`, and `call_webhook_url`
(exact public `/webhooks/call` status URL with the same scope). Keep this JSON
out of Git and ordinary deployment logs. Only grant the API and outreach worker
access to their respective copies. If the Railway plan's volume limit prevents
both mounts, use an external protected secret volume; do not put credentials in
source control or task payloads.

An authorized caller can submit a call with `POST /voice/calls`, a
`campaign_writer` API key, an `Idempotency-Key` header, and JSON containing
`business_id` plus a provisioned `contact_id`. The API persists the request in
the durable queue; the outreach worker reloads the contact and rechecks call
policy before calling Twilio. The endpoint does not accept a caller phone
number, callback URL, or tenant ID from request data.
Voice task failures go to the durable dead-letter state after one attempt.
Before an authorized operator redrives an ambiguous task, reconcile it against
Twilio call history to avoid placing a duplicate after a provider timeout.

Railway's injected Postgres URL may require a driver prefix and an explicitly
verified TLS configuration to meet this application's production checks.
Do not weaken those checks to make deployment start. A private Railway Redis
URL must also satisfy the required `rediss://` transport. If Railway's managed
database or broker does not provide the required TLS verification, use a
TLS-capable managed service in the same approved region.

## Region and data residency

Set the Railway service's physical region as well as the application's
`DEPLOYMENT_REGION_ID`. Railway's current service regions include US West
(`us-west2`), US East (`us-east4-eqdc4a`), and EU West
(`europe-west4-drams3a`). Map physical placement to the corresponding
application region only after confirming the managed database and Redis are
in that same approved geography. Railway does not currently list a Canada or
Africa service region; do not place tenant data there under an unrelated
region label. See the [Railway regions guide](https://docs.railway.com/deployments/regions).

Run migration `alembic upgrade head` once as a separate Railway one-off deploy
job or from a controlled deployment environment before starting workers. Do
not run migrations from each replica. Configure durable volumes/backups,
retention, and alerts for the database and broker through the selected service
providers. Worker queue records in PostgreSQL remain authoritative; Redis is
the delivery broker.

## Verify the deployment

Start one inbound worker first and confirm its Railway logs show Celery ready
and that it subscribes to `agent.control`, `agent.inbound`, and the selected
region's `agent.<region>.inbound` queue. Start the remaining domain workers and
then the single Beat service. Check the authenticated
`GET /health/components` endpoint for worker heartbeat and queue readiness.
Submit an idempotent task in a non-production tenant and verify its persisted
queue history, event trace, and expected result before routing production
traffic. Provider-backed tasks require sandbox adapter configuration and
policy-approved test contacts.
