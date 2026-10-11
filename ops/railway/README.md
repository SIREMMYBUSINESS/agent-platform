# Railway deployment

## Runtime layout

Run the API and one Python worker as separate services. Both connect to the
same PostgreSQL database in the approved region. The Remix application on
Cloudflare Workers calls the API; it must not connect directly to PostgreSQL,
so tenant authorization, policy checks, transactions, and provider adapters
remain behind the application service boundary.

The API persists background work to `queue_task_records` in the same
transaction as its domain changes. The Python worker polls that table every
five seconds, claims due records under a database transaction, and executes
the existing domain handlers. Retry counts, leases, idempotency keys, results,
and dead letters remain in PostgreSQL. APScheduler also runs the daily feedback
optimization at 03:00 UTC. No Redis, Celery broker, or separate Beat service is
needed.

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
   - `DATABASE_URL` to the PostgreSQL service's private connection URL, with
     the SQLAlchemy `psycopg` driver and `sslmode=verify-full`
   - `DATABASE_REGION_ID` and `DEPLOYMENT_REGION_ID` to the same supported
     application region ID, matching the physical Railway region

   Keep `DATABASE_URL` in Railway variables. The API and worker entry points
   move an injected URL into a private, read-only temporary file before loading
   production settings. Do not print the URL or commit it to the repository. If the managed
   database endpoint cannot meet the certificate and hostname verification
   requirement, use a TLS-capable PostgreSQL provider in the same region.

5. Run `python -m app.runtime_entry migrate` once as a controlled one-off job
   after the database is ready and before starting the worker. This entry point
   first moves Railway's injected database URL to a protected temporary file.
   Do not run migrations from each service replica.
6. Configure `PROVIDER_SECRETS_DIR` and protected secret files only for
   services that need provider credentials. Keep those files out of Git and
   logs.

## Verify

- `/health` confirms the API process is running.
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
