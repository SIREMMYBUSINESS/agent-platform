# Railway deployment

## Runtime layout

The target layout runs the API and one Python worker as separate services.
Both connect to the same PostgreSQL database in the approved region. The
currently inspected free-plan Railway deployment runs both processes in the
`agent-platform` service: its start command backgrounds
`python -m app.runtime_entry worker` and then starts the API. The API and worker
service configurations are prepared separately in
`ops/railway/api.railway.json` and `ops/railway/worker.railway.json`; selecting
these config files for new Railway services is a dashboard/service setup step.
The root `railway.json` remains the current combined-service deployment config
until a staged cutover is ready. The Remix
application on Cloudflare Workers calls
the API; it must not connect directly to PostgreSQL, so tenant authorization,
policy checks, transactions, and provider adapters remain behind the
application service boundary.

The API persists background work to `queue_task_records` in the same
transaction as its domain changes. The Python worker polls that table every
five seconds, claims due records under a database transaction, and executes
the existing domain handlers. Retry counts, leases, idempotency keys, results,
and dead letters remain in PostgreSQL. APScheduler also runs the daily feedback
optimization at 03:00 UTC. No Redis, Celery broker, or separate Beat service is
needed.

## Single-region settings

Keep these explicit on every Railway API/worker service and in CI:

```dotenv
TENANT_REGION_ROUTING_ENABLED=false
DATABASE_REGION_ID=eu-west-1
DEPLOYMENT_REGION_ID=eu-west-1
```

`eu-west-1` is this application's logical region label for the current Amsterdam
deployment. Railway calls the physical region `europe-west4-drams3a`; do not put
that provider-specific identifier into these application settings. These labels
do not move data or independently verify the database's physical location.

For the currently combined Railway service, one set of variables covers both
processes. When split, copy these three settings to both API and worker
services and give both credentials only for that region's database.

Startup rejects an explicitly configured `DATABASE_REGION_ID` that differs
from `DEPLOYMENT_REGION_ID`, even with routing disabled. Development can omit
the database region; production must provide a matching region and a protected,
certificate-verified database URL. Enabling routing also requires a separate
control database, but satisfying startup validation does not satisfy rollout
gates or perform migrations. Keep the flag disabled until those gates pass.

The `Regional safety` GitHub workflow uses the three defaults above and runs
the suite with Python 3.12. Its PR guard checks literal routing enablement in
configuration/deployment files and the Python settings default. Enablement
requires both a non-deleted migration under `migrations/versions/` and an ops
update under `ops/` in the same PR. This is a review tripwire; accompanying files
do not prove the migration or operational plan is correct. Runtime overrides
made directly in Railway are outside the PR guard. Make the workflow checks
required in branch protection to prevent merging a failing PR.

`GET /health/runtime` returns only `environment`, `deployment_region_id`,
`database_region_id`, and `tenant_region_routing_enabled`. It reports the
configuration loaded by that API process; it does not query the database or
prove worker/provider health. Use `/ready` and `/health/components` for those
dependency checks. Saved Railway variables take effect in a new deployment;
editing them with `--skip-deploys` does not change an already running process.

## Separate API and worker services

After the Regional safety checks pass and an isolated Railway staging
environment is available, create a worker service from this repository. Set
its Railway config file to `/ops/railway/worker.railway.json`; it builds
`Dockerfile.worker`, whose command starts only the PostgreSQL-backed worker.
Set the public API service config file to `/ops/railway/api.railway.json`; it
builds `Dockerfile` and starts only the API. Do not add a public domain to the
worker. Copy the
staging database and matching logical region settings to both services, then
remove the combined `worker & api` start command only after both new services
pass the staging smoke checks. Railway Free sleep behavior remains enabled;
this split does not guarantee an always-on worker on the free plan.

This is a single-region deployment layout. Railway's multi-region replicas
must not be enabled for this app until each regional process has a dedicated
database, the global directory/auth schema is provisioned, tenant routing is
implemented for every request path, and migration/recovery gates have passed.
The current routing flag remains false.

## Gated regional rollout plan

Keep `TENANT_REGION_ROUTING_ENABLED=false` throughout preparation. Implement
the following proposed flags separately; they are planning names and do not
exist as settings yet. Each phase starts with a synthetic tenant and a disabled
flag, and requires an explicit rollback procedure before broader activation.

| Phase | Proposed flag | Enablement evidence | Stop or rollback |
| --- | --- | --- | --- |
| Control database split | `CONTROL_DIRECTORY_AUTH_ENABLED` | Dedicated minimal schema, copied key/grant metadata, tenant-assignment parity, revocation checks, independent readiness | Stop canary traffic and restore the previous authentication path only after revocation parity is confirmed |
| Token routing and forwarding | `REGIONAL_TOKEN_FORWARDING_ENABLED` | Verified capability-to-home-region mapping, authenticated regional hop, bounded forwarding, replay/expiry tests, no fallback to another database | Disable forwarding; return an explicit unavailable response for affected token actions |
| Tenant migration and recovery tooling | `TENANT_MIGRATION_ENABLED` | Audited state machine, pause/drain controls, tenant-scoped export/import, checksums, provider-free queue canary, rehearsed restore/rollback | Keep the tenant in migrating state; reconcile writes before restoring any source assignment |

Only after all gates pass should the routing flag be enabled for an isolated
canary deployment. Confirm regional API/worker/database isolation, schema head,
directory behavior, and queue completion before expanding. Provider validation
remains a separate release gate. See the [architecture decision](../architecture/tenant-regional-data-plane.md)
and [migration/recovery runbook](../runbooks/regional-tenant-migration-recovery.md).

## Create the services

1. Add PostgreSQL to the project and place it in the same approved region as
   the API and worker. Attach persistent storage and enable backups.
2. Connect this repository as the API service, with the repository root as its
   root directory and `Dockerfile` as its Dockerfile.
3. Add one service from the same repository for the worker and set
   `RAILWAY_DOCKERFILE_PATH=/Dockerfile.worker`. The image starts
   `python -m app.workers.railway_entry`. Do not add a public domain.
4. Set the following variables on the API and worker:

   - `ENVIRONMENT=production`
   - `TENANT_REGION_ROUTING_ENABLED=false`
   - `DATABASE_URL` to the PostgreSQL service's private connection URL, with
     the SQLAlchemy `psycopg` driver and `sslmode=verify-full`
   - `DATABASE_REGION_ID` and `DEPLOYMENT_REGION_ID` to the same supported
     application region ID, matching the physical Railway region
   - `DATABASE_SSL_ROOT_CERT_B64` to the base64-encoded PEM root CA from the
     PostgreSQL service's `/var/lib/postgresql/data/certs/root.crt`

   Keep `DATABASE_URL` in Railway variables. The API and worker entry points
   move an injected URL into a private, read-only temporary file before loading
   production settings. They also materialize the CA into a private file and
   set `PGSSLROOTCERT` for libpq certificate verification. Do not print the URL
   or CA value or commit either to the repository. If the managed database
   endpoint cannot meet the certificate and hostname verification requirement,
   use a TLS-capable PostgreSQL provider in the same region.

5. Run `python -m app.runtime_entry migrate` once as a controlled one-off job
   after the database is ready and before starting the worker. This entry point
   first moves Railway's injected database URL to a protected temporary file.
   Do not run migrations from each service replica.
6. Configure `PROVIDER_SECRETS_DIR` and protected secret files only for
   services that need provider credentials. Keep those files out of Git and
   logs.

## Verify

- `/health` confirms the API process is running.
- `/health/runtime` confirms the API's loaded region settings and routing flag.
- `/ready` confirms PostgreSQL can answer a query.
- `/health/components` must report `database: healthy` and `worker: healthy`.
  The worker writes a heartbeat every ten seconds; the API treats heartbeats
  older than thirty seconds as unhealthy.
- Submit an idempotent task in a non-production tenant and verify its persisted
  status, retry history, and result before routing production traffic.
- Confirm migrations are at the expected Alembic head and provider-backed
  handlers use sandbox credentials and policy-approved test records.

The existing Railway Redis service is not part of this runtime. Leave it in
place during migration; remove it only after the API and worker are deployed
without Redis, queued work has been reconciled, and its credentials have been
rotated.


## Cloudflare frontend connectivity

The Cloudflare app's build-time `VITE_PLATFORM_API_URL` must be set to the Railway API's public HTTPS origin (scheme and host only). The exact Cloudflare site origin belongs in Railway's `CORS_ALLOWED_ORIGINS` JSON list, for example:

```text
CORS_ALLOWED_ORIGINS=["https://<cloudflare-staging-site-host>"]
```

This is a JSON array, not a comma-separated string. Use the full origin with scheme and optional port, and no path or wildcard. The settings validator rejects public HTTP origins. Set a stable staging hostname in Cloudflare and restart the Railway API after changing this variable. The dashboard proxy is server-to-server; the onboarding flow currently calls Railway from the browser and requires the CORS entry.

For staging commands and the preflight assertions, see [the web app staging smoke checks](../../web/README.md#staging-smoke-checks). Never put `PLATFORM_SERVICE_TOKEN` in a `VITE_*` variable. The API base URL is public configuration; service tokens, `SESSION_ENCRYPTION_KEY`, Railway URLs, and provider credentials remain secret.
