# Railway staging smoke checks

Run this checklist only in a dedicated staging environment with an isolated PostgreSQL database and synthetic tenant/business/contact records. Never point it at the production API or database. Keep `TENANT_REGION_ROUTING_ENABLED=false`; these checks do not validate multi-region forwarding or provider integrations.

## Environment

On the Railway staging environment, verify:

```dotenv
TENANT_REGION_ROUTING_ENABLED=false
DATABASE_REGION_ID=eu-west-1
DEPLOYMENT_REGION_ID=eu-west-1
```

The region IDs are application labels. Confirm the PostgreSQL service's physical Railway region in Railway settings; `/health/runtime` does not prove where the database is hosted. Use the staging PostgreSQL URL, never the production URL. Keep provider secrets unset for this provider-free check.

After deploying the PR build, run `python -m app.runtime_entry migrate` once as a Railway one-off job explicitly scoped to the staging service and staging database. Confirm `alembic_version.version_num` is `0030_tenant_db_route` in that staging database. Do not point this command at production or a future control database; the current migration entry point targets `DATABASE_URL` only.

## API, database, and worker

After deploying the candidate API and worker, run from PowerShell 7:

```powershell
.\scripts\smoke_railway_staging.ps1 `
  -BaseUri 'https://<staging-host>' `
  -ExpectedRegion 'eu-west-1' `
  -ConfirmStaging
```

The script retries `/ready` for up to two minutes to allow a sleeping free-plan service to wake. It then requires:

- `/ready`: `200` and `status=ready`;
- `/health/runtime`: `200`, routing disabled, and deployment/database region IDs both `eu-west-1`;
- `/health/components`: `200`, overall/database/worker health all `healthy`, and providers `not_probed`.

If Railway starts the API and worker as separate services, run the runtime-region check against both and verify each is connected only to the staging database for that region.

## Provider-free durable queue task

Use an already provisioned synthetic staging tenant with an `inbound_writer` API key, business, and contact. Use channel `chat`; do not include real customer data, Twilio credentials, or a real phone/email destination.

```powershell
$base = 'https://<staging-host>'
$secret = Read-Host 'Staging API key' -AsSecureString
$apiKey = [System.Net.NetworkCredential]::new('', $secret).Password
$idempotencyKey = [guid]::NewGuid().ToString()
$body = @{
  tenant_id = '<synthetic-tenant-id>'
  business_id = '<synthetic-business-id>'
  contact_id = '<synthetic-contact-id>'
  channel = 'chat'
  text = "Provider-free staging queue check $idempotencyKey"
  contact_name = 'Staging Smoke Test'
} | ConvertTo-Json

$headers = @{
  Authorization = "Bearer $apiKey"
  'Idempotency-Key' = $idempotencyKey
}
$queued = Invoke-RestMethod -Method Post -Uri "$base/inbound/queue" `
  -Headers $headers -ContentType 'application/json' -Body $body
$taskId = [string]$queued.id
if (-not $taskId -or $queued.status -notin @('pending', 'queued', 'running', 'retrying', 'succeeded')) {
  throw 'The provider-free inbound task was not accepted.'
}
Write-Output "Accepted task $taskId with initial status $($queued.status)."

# Repeating the exact request with the same key must return the same task ID.
$duplicate = Invoke-RestMethod -Method Post -Uri "$base/inbound/queue" `
  -Headers $headers -ContentType 'application/json' -Body $body
if ([string]$duplicate.id -ne $taskId) {
  throw 'Idempotency check returned a different task ID.'
}
Remove-Variable apiKey, secret, headers
```

The API currently has no task-status read endpoint. Verify completion using an approved read-only query against the **staging** database, or the authorized staging database console. Substitute the UUID returned as `$taskId`; select no payload or result content:

```sql
SELECT id, status, attempts, error_code, region_id
FROM queue_task_records
WHERE id = '<task-id>'::uuid;
```

Pass when the row reaches `succeeded`, `attempts` is `1`, and the duplicate submission still returned the same ID. A retrying/dead-letter task fails the smoke check and should be investigated from sanitized worker logs before repeating with a new idempotency key.

## Stop conditions and later gates

- Stop if any endpoint reports the wrong region, routing enabled, unhealthy DB/worker, or an unexpected provider probe.
- Do not test a real provider webhook as part of this run. Provider validation is a separate release gate requiring approved sandbox credentials and synthetic provider destinations.
- Do not enable tenant-region routing, apply a control-database migration, or move data as part of this staging smoke check.
- Record deployment ID, commit SHA, test tenant ID, task ID, result, and timestamp. Do not record API keys, database URLs, provider secrets, or customer content.
