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

## Cloudflare and API configuration

In Cloudflare's **build environment variables**, set the staging Worker's values to:

```text
VITE_PLATFORM_API_URL=https://<render-staging-api-host>
VITE_SITE_URL=https://<cloudflare-staging-site-host>
VITE_DEMO_BOOKING_URL=
```

Use origins only: no path and no trailing slash. `VITE_PLATFORM_API_URL` is a public API address compiled into the Worker bundle at build time; it is not a secret and must not contain credentials. Staging uses the Render API; production uses the Railway API in its own Worker configuration. Rebuild and deploy the Worker after changing it. Do not set `PLATFORM_SERVICE_TOKEN` as a `VITE_*` variable or expose it to the browser. Since the Worker proxies authenticated requests server-side, this web app does not need a browser CORS allowlist on the API.

Dashboard and onboarding requests go through React Router server loaders/actions on the Worker and do not need browser CORS. No `PLATFORM_SERVICE_TOKEN` is used. Onboarding reuses the encrypted workspace session; the tenant key must also have the `business_onboarder` role. The browser never receives or stores the raw API key.

Set `SESSION_ENCRYPTION_KEY` as a Cloudflare Worker secret (for example, from `web/`, run `npx wrangler secret put SESSION_ENCRYPTION_KEY`). Use a distinct key for staging and production. Do not use `VITE_SESSION_ENCRYPTION_KEY`.

## Workspace session security

The dashboard posts a provisioned tenant API key to a React Router server action. The Worker validates tenant-scoped read access against the configured API, encrypts the key with AES-GCM, and keeps it only in an HttpOnly, Secure, SameSite=Strict cookie that expires after one hour. Server loaders/actions proxy dashboard and onboarding requests; browser JavaScript never receives the stored key and no API credential is written to localStorage. Onboarding additionally requires the `business_onboarder` grant. The key is encrypted but remains a tenant credential, so use appropriately limited grants and rotate/revoke it when needed.

The current backend uses tenant-scoped `ap_` API keys. The dashboard requires both `lead_reader` and `conversation_reader` grants; the backend does not expose the proposed `read:seo_pages` or `create:seo_lead_submissions` scopes. The backend has no public SEO-page data endpoint or anonymous lead-submission endpoint yet.

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

Then sign in to `$site/dashboard` with a staging-only tenant key that has `lead_reader`, `conversation_reader`, and `business_onboarder` grants. Open `$site/onboarding` and load the dental template using the same session; the browser Network panel should show same-origin Worker requests, not direct API requests. Never use a production key. The API health checks do not validate third-party providers or a queue task; run those as separate staging checks.

## Cloudflare and Railway

From this directory, `npm run check` typechecks, builds, and dry-runs the Worker; `npm run deploy` builds and deploys. Add the exact frontend origin to Railway CORS only for browser-to-API calls such as the current onboarding flow. The Worker never connects to PostgreSQL.
