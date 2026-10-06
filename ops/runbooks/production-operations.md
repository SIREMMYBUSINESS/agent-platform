# Production operations runbook

## PostgreSQL migration and validation

Use a dedicated disposable database whose name ends in `_test` for the live migration suite. The fixture creates and drops a unique temporary schema in that database and refuses other database names.

```powershell
$env:POSTGRES_TEST_DATABASE_URL = 'postgresql+psycopg://test_user:...@test-db/agent_platform_test?sslmode=verify-full'
.\.venv\Scripts\python.exe -m pytest -v -m postgres tests\test_postgres_integration.py
```

For an environment deployment, first take and verify a managed PostgreSQL backup, then apply the additive Alembic migrations as a one-off job. Do not run migration commands from every application worker.

```powershell
python -m alembic current
python -m alembic upgrade head
python -m alembic current
```

Production configuration rejects database URLs without `sslmode=verify-full` or `verify-ca`, and requires `DATABASE_URL_FILE`. Prefer `verify-full` with the managed database CA configured in the platform image. After migration, check `/ready`, run the deployment smoke checks, and confirm the expected Alembic head before enabling traffic. Restore backups into an isolated database and validate them before relying on them.

## Secrets provisioning and rotation

Set `DATABASE_URL_FILE` and `REDIS_URL_FILE` to mounted secret files. In production, files must be regular files, non-symlinks, non-writable, and readable only by the service owner or its configured group. Database URLs must use certificate-verified PostgreSQL TLS; Redis URLs must use `rediss://`.

Provider configuration is JSON in a read-only mounted directory. The filename is lowercase SHA-256 of `tenant_id + NUL + business_id`, followed by `.json`. Files contain string values required by the provider adapters, for example:

```json
{
  "account_sid": "<Twilio Account SID>",
  "auth_token": "<secret from the secret manager>",
  "from_sms": "+15550000000",
  "status_callback_base_url": "https://api.example.com/webhooks/twilio"
}
```

Use mode `0400` for an owner-readable file, or `0440` with the file group set to the service group. Rotate by atomically replacing the mounted file. During Twilio auth-token rotation, include the new `auth_token` and the previous token as `auth_token_previous` during the provider callback transition window, then remove the previous token. The resolver reads current files on each operation and never logs their contents. Keep these files out of source control, container layers, and support bundles.

## Twilio delivery receipts

Set `status_callback_base_url` to the public HTTPS base ending in `/webhooks/twilio`. The adapter appends the tenant, business, and persisted outreach-message UUID. Configure the public proxy to forward the callback path and query unchanged and terminate TLS. Signature validation uses this configured URL and the current tenant/business auth token; incoming `Host` and forwarded-host headers are not trusted. Twilio status callbacks are form-encoded and can add fields over time, so preserve all callback parameters during signature validation.

Confirm each sent SMS/WhatsApp record gets a provider SID, then check delivery transitions in the outreach record and campaign audit events. Duplicate and out-of-order callbacks are safe to replay; terminal state is not downgraded. Do not expose callback request bodies or auth tokens in proxy logs.

## Deployment and smoke checks

Build the image from the repository root. The container runs as UID/GID `10001`, has a read-only root filesystem, and checks database readiness at `/ready`. The example Compose file expects external PostgreSQL and Redis services, mounted secret files, and a provider-secret directory. Put it behind a TLS-terminating proxy; the published app port binds to loopback.

```powershell
docker compose -f ops/compose.production.yml.example config
docker compose -f ops/compose.production.yml.example build
docker compose -f ops/compose.production.yml.example run --rm migrate
docker compose -f ops/compose.production.yml.example up -d app
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

`/health` is liveness only. `/ready` runs `SELECT 1` and returns a generic 503 while PostgreSQL is unavailable. `/metrics` requires an authenticated `metrics_reader` principal. Verify metrics scraping, callback ingress over HTTPS, image health, and alert/dashboard connectivity before directing customer traffic. On rollback, prefer the last known-good image; use `alembic downgrade` only after reviewing the migration and a tested restore plan.
