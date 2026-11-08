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

## Configure isolated provider smoke tests

Provider credentials belong only in Render's staging secret files. The
Blueprint configures the read-only provider directory at `/etc/secrets` and
loads the tenant-scoped provider bootstrap. The Docker image adds its service
user to Render's secret-file group so the process can read those files without
running as root.

Each Render provider secret file must be named `scope-` followed by the SHA-256
hex digest of the tenant ID, a NUL byte, and the business ID, followed by `.json`.
The prefix avoids Render's restriction on filenames starting with a digit.
Existing unprefixed filenames remain supported and take precedence. Generate the
filename locally (the IDs are not secrets):

```powershell
python -c "import hashlib,sys; print('scope-'+hashlib.sha256((sys.argv[1]+'\0'+sys.argv[2]).encode()).hexdigest()+'.json')" "<tenant-id>" "<business-id>"
```

In Render, open the staging service's **Environment → Secret Files** and add
that filename. Use a JSON object with only the providers being tested. Expected
keys are:

- Twilio SMS: `sms_provider` (`twilio`), `account_sid`, `api_key_sid`,
  `api_key_secret`, and either `from_sms` or `messaging_service_sid`. The
  adapter also accepts the legacy `auth_token` pair, but prefer a revocable
  Standard API key for application sends. Add `auth_token` only if you enable
  signed delivery-status callbacks; callbacks require the account Auth Token.
- SendGrid email: `email_provider` (`sendgrid`), `api_key`, and `from_address`.
- Google Calendar: `calendar_provider` (`google`), `access_token`, and
  `calendar_id`.

Do not put credentials in GitHub, the Worker, browser storage, or chat. Use a
verified, owner-controlled test phone and inbox, and a dedicated staging
calendar. Validate one SMS, one email, calendar availability, then one
test-calendar booking/cancellation. Do not enroll or trigger a campaign against
a customer audience. Provider health remains `not_probed` until provider
operations have actually run; API and database health do not prove delivery.

## Deploy the staging Worker

Create the GitHub Actions environment `staging`, then add these there before
dispatching `.github/workflows/web-staging-deploy.yml` from the feature branch:

- Actions secret `CLOUDFLARE_API_TOKEN`, scoped to deploy Workers in this
  account.
- Actions variable `CLOUDFLARE_ACCOUNT_ID`.

The workflow asks for the isolated Render API origin and optional Cloudflare
site origin. It rejects non-HTTPS or non-Render API origins and deploys under
the separate Worker name `agent-platform-web-staging`, matching the staging
URL `https://agent-platform-web-staging.<account>.workers.dev`. It runs
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
