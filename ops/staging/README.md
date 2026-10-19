# Short-run staging

This staging path keeps the Cloudflare frontend, FastAPI service, and test
database separate from the live Railway deployment. It is for short smoke
checks only. It is not an always-on or customer-data environment.

## Create the isolated API and database

1. Create a Neon Free project in Frankfurt. Do not copy production records.
   Use the Neon connection string with TLS enabled and the `psycopg` SQLAlchemy
   driver, for example `postgresql+psycopg://...?...sslmode=require`.
2. In Render, create a Blueprint from this repository and select
   `render.yaml`. It creates one free web service in Frankfurt using the
   `feat/agent-workspace-dashboard` branch and `Dockerfile`. If Render asks
   for a region, use Frankfurt to keep the API close to the Neon database.
3. Enter the staging Neon connection string when Render prompts for
   `DATABASE_URL`. Keep it in Render's secret environment settings; do not add
   it to GitHub or a local checked-in file.
4. Wait for `/ready` to pass, then check `/health/components`. The service runs
   the Alembic upgrade at startup and starts one queue poller alongside the
   API. No provider secrets are configured, so provider health remains
   unprobed. Use only synthetic tenants and data.

The Render Free web instance sleeps after 15 minutes without inbound traffic.
Because the queue poller shares that process, it stops while the service sleeps.
Use this deployment only for a short enqueue-to-completion smoke check while
the API is awake. Do not rely on it for continuous queue processing.

## Deploy the staging Worker

Create the GitHub Actions environment `staging`, then add these there before
dispatching `.github/workflows/web-staging-deploy.yml` from the feature branch:

- Actions secret `CLOUDFLARE_API_TOKEN`, scoped to deploy Workers in this
  account.
- Actions variable `CLOUDFLARE_ACCOUNT_ID`.

The workflow asks for the isolated Render API origin and optional Cloudflare
site origin. It rejects non-HTTPS or non-Render API origins and deploys under
the separate Worker name `siremmy-agent-platform-web-staging`. It runs
`npm run check` before deploying. Never use the live Railway API origin here.

After the first deploy, record the actual `workers.dev` origin. Set the
Worker's `SESSION_ENCRYPTION_KEY` as a Cloudflare secret, not a build variable.
Then add the exact Worker origin to Render as
`CORS_ALLOWED_ORIGINS=["https://<staging-worker-host>"]`, redeploy, and dispatch
the frontend workflow again with that `site_origin` so canonical URLs are
correct.

## Smoke checks

From PowerShell, substitute the actual public origins:

```powershell
$api = "https://<render-staging-host>.onrender.com"
$site = "https://<staging-worker-host>.workers.dev"

$ready = Invoke-RestMethod "$api/ready"
$components = Invoke-RestMethod "$api/health/components"
if ($ready.status -ne "ready") { throw "Staging API/Postgres readiness failed" }
if ($components.status -ne "healthy") { throw "Staging API/worker health failed" }

$preflight = Invoke-WebRequest -Method Options -Uri "$api/verticals" -Headers @{
  Origin = $site
  "Access-Control-Request-Method" = "GET"
  "Access-Control-Request-Headers" = "authorization,content-type"
}
if ($preflight.StatusCode -ne 200 -or
    [string]$preflight.Headers["Access-Control-Allow-Origin"] -ne $site) {
  throw "Staging CORS preflight failed"
}

foreach ($path in @("/", "/ai-receptionist/", "/dashboard", "/sitemap.xml", "/robots.txt")) {
  $response = Invoke-WebRequest "$site$path"
  if ($response.StatusCode -ne 200) { throw "$path returned $($response.StatusCode)" }
}
```

Render Free does not provide service shell access or one-off jobs, so run the
queue smoke from GitHub Actions:

1. In GitHub, open **Settings → Environments → staging** and add the
   environment secret `STAGING_DATABASE_URL` using the isolated Neon staging
   connection string. Do not use the Railway production URL.
2. Open **Actions → Staging queue smoke → Run workflow** and select
   `feat/agent-workspace-dashboard` (or the branch containing this workflow).
3. The workflow wakes the Render service, verifies `/ready` and
   `/health/components`, then creates one synthetic tenant, business, and
   contact, enqueues an inbound `chat` task, and polls its durable queue record
   until the Render worker marks it `succeeded` (up to three minutes).

The command refuses to run outside `ENVIRONMENT=staging`, without the
workflow's explicit opt-in, or unless the database hostname belongs to Neon.
Synthetic records remain in the isolated staging database for audit. The task
contains no phone number or email and does not invoke SMS, email, voice, or
other external providers. Provider validation remains a separate gate.

If it times out, check the Render service logs and rerun the workflow. The Free
service may sleep again after inactivity.
