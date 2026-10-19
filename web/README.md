# Agent Platform web app

The public SEO site and tenant workspace use React Router with Cloudflare's Vite plugin on Cloudflare Workers. Production FastAPI and PostgreSQL remain on Railway.

For the temporary free staging layout using Cloudflare Workers, Render, and Neon,
see [the short-run staging runbook](../ops/staging/README.md). It is isolated from
the live Railway deployment and does not provide an always-on queue worker.

## Local development

Copy `.env.example` to `.env`, create `.dev.vars` with a random 32-byte base64 `SESSION_ENCRYPTION_KEY`, then run:

```powershell
npm install
npm run dev
```

Set `VITE_PLATFORM_API_URL` in `.env` to the Railway API's HTTPS origin. Set `VITE_SITE_URL` to the canonical public site origin and `VITE_DEMO_BOOKING_URL` to the real walkthrough booking page before publishing. These are public build configuration. Never prefix secrets with `VITE_`.

## Cloudflare and Railway staging configuration

In Cloudflare's **build environment variables** for the staging Worker, set:

```text
VITE_PLATFORM_API_URL=https://<railway-staging-api-host>
VITE_SITE_URL=https://<cloudflare-staging-site-host>
VITE_DEMO_BOOKING_URL=
```

Use origins only: no path and no trailing slash. `VITE_PLATFORM_API_URL` is a public API address compiled into the Worker bundle at build time; it is not a secret and must not contain credentials. Rebuild and deploy the Worker after changing it. Do not set `PLATFORM_SERVICE_TOKEN` as a `VITE_*` variable or expose it to the browser.

In Railway's API service variables, set `CORS_ALLOWED_ORIGINS` to a JSON array containing the exact Cloudflare site origin:

```text
CORS_ALLOWED_ORIGINS=["https://<cloudflare-staging-site-host>"]
```

The repo parses this as `list[str]` and rejects wildcards, paths, credentials, and non-HTTPS public origins. Do not use a comma-separated value. Restart/redeploy the API after changing it. Allow the Cloudflare **site origin**, not the Railway API URL. A stable staging hostname is preferable to short-lived preview URLs.

The onboarding screen currently calls Railway directly from the browser, so it needs this CORS allowlist. Dashboard data reads go through React Router server loaders/actions on the Worker and do not need CORS. No `PLATFORM_SERVICE_TOKEN` is used. Onboarding currently accepts a tenant-scoped API key from the user; that is separate from a platform service token. If the requirement is that no API credential at all may enter browser memory, move onboarding requests behind the Worker proxy before customer staging.

Set `SESSION_ENCRYPTION_KEY` as a Cloudflare Worker secret (for example, from `web/`, run `npx wrangler secret put SESSION_ENCRYPTION_KEY`). Use a distinct key for staging and production. Do not use `VITE_SESSION_ENCRYPTION_KEY`.

## Workspace session security

The dashboard posts a provisioned tenant API key to a React Router server action. The Worker validates tenant-scoped read access against the Railway API, encrypts the key with AES-GCM, and keeps it only in an HttpOnly, Secure, SameSite=Strict cookie that expires after one hour. Server loaders proxy dashboard reads; browser JavaScript never receives the stored key and no API credential is written to localStorage. The key is encrypted but remains a tenant credential, so use appropriately limited grants and rotate/revoke it when needed.

The current backend uses tenant-scoped `ap_` API keys and `lead_reader` grants; it does not expose the proposed `read:seo_pages` or `create:seo_lead_submissions` scopes. The backend has no public SEO-page data endpoint or anonymous lead-submission endpoint yet.

## Public SEO routes

The site includes:
- `/` — solution index
- `/ai-receptionist/`
- `/ai-receptionist/real-estate/`
- `/ai-receptionist/dental/`
- `/ai-appointment-scheduling/`
- `/ai-customer-service/`
- `/ai-sales-outreach/`
- `/onboarding` — existing onboarding flow
- `/dashboard` — authenticated tenant workspace

Pages provide unique titles/descriptions, canonical links when `VITE_SITE_URL` is set, social metadata, relevant internal links, FAQ structured data, and sitemap/robots routes. Copy describes configurable workflows and avoids unsupported outcome, provider, and compliance claims.

No public lead form is enabled because the backend currently has no anonymous lead endpoint. Before collecting submissions, add a validated backend endpoint with abuse controls, server-side Cloudflare Turnstile verification, and rate limiting. Verify Turnstile tokens on the server, never in client code alone.

## Staging smoke checks

Run from PowerShell 7 after both services are deployed. Replace the two placeholders with the actual public HTTPS origins; do not add paths or trailing slashes.

```powershell
$api = "https://<railway-staging-api-host>"
$site = "https://<cloudflare-staging-site-host>"

$health = Invoke-RestMethod "$api/health"
$ready = Invoke-RestMethod "$api/ready"
$components = Invoke-RestMethod "$api/health/components"
if ($health.status -ne "ok") { throw "API health failed" }
if ($ready.status -ne "ready") { throw "PostgreSQL readiness failed" }
if ($components.status -ne "healthy") { throw "API or worker component is unhealthy" }

$preflight = Invoke-WebRequest -Method Options -Uri "$api/verticals" -Headers @{
  Origin = $site
  "Access-Control-Request-Method" = "GET"
  "Access-Control-Request-Headers" = "authorization,content-type"
}
$allowedOrigin = [string]$preflight.Headers["Access-Control-Allow-Origin"]
if ($preflight.StatusCode -ne 200 -or $allowedOrigin -ne $site) {
  throw "CORS preflight failed: status=$($preflight.StatusCode), allow-origin=$allowedOrigin"
}

foreach ($path in @(
  "/",
  "/ai-receptionist/",
  "/ai-receptionist/real-estate/",
  "/ai-receptionist/dental/",
  "/ai-appointment-scheduling/",
  "/ai-customer-service/",
  "/ai-sales-outreach/",
  "/sitemap.xml",
  "/robots.txt"
)) {
  $response = Invoke-WebRequest "$site$path"
  if ($response.StatusCode -ne 200) { throw "$path returned $($response.StatusCode)" }
}
"API, database, worker, CORS preflight, public routes, sitemap, and robots checks passed."
```

Then open `$site/onboarding` in a browser and load the dental template with a staging-only tenant key. Confirm the browser Network panel shows requests to the configured Railway origin with the exact staging site as `Origin`; never use a production key. For `/dashboard`, verify its data requests are same-origin Worker requests and not direct browser-to-Railway requests. The API health checks do not validate third-party providers or a queue task; run those as separate staging checks.

## Cloudflare and Railway

From this directory, `npm run check` typechecks, builds, and dry-runs the Worker; `npm run deploy` builds and deploys. Add the exact frontend origin to Railway CORS only for browser-to-API calls such as the current onboarding flow. The Worker never connects to PostgreSQL.
