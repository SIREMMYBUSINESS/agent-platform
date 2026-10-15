# Agent Platform web app

The public site and tenant workspace use React Router with Cloudflare's Vite plugin on Cloudflare Workers. FastAPI and PostgreSQL remain on Railway.

## Local development

Copy `.env.example` to `.env`, create `.dev.vars` with a random 32-byte base64 `SESSION_ENCRYPTION_KEY`, then run:

```powershell
npm install
npm run dev
```

Set `VITE_PLATFORM_API_URL` in `.env` to the Railway API's HTTPS origin. This API origin is public configuration. Never prefix secrets with `VITE_`.

## Workspace session security

The dashboard posts a provisioned API key to a React Router server action. The Worker validates read access against the tenant-scoped Railway API, encrypts the key with AES-GCM, and returns it only in an HttpOnly, Secure, SameSite=Strict cookie that expires after one hour. Server loaders use the decrypted key for API calls; browser JavaScript never receives the key from a response and no API credential is written to localStorage.

Set `SESSION_ENCRYPTION_KEY` as a Cloudflare Worker secret (for example, with `npx wrangler secret put SESSION_ENCRYPTION_KEY`). Use a per-environment key. The current backend uses tenant-scoped `ap_` API keys and `lead_reader` grants; it does not expose the proposed `read:seo_pages` or `create:seo_lead_submissions` scopes.

Do not create or expose a `PLATFORM_SERVICE_TOKEN` in browser code or any `VITE_*` variable. This frontend does not use a shared service token. The existing backend has no public SEO-page read or anonymous lead-submission endpoints yet.

## Public SEO routes

The site includes:
- `/ai-receptionist/`
- `/ai-receptionist/real-estate/`
- `/ai-receptionist/dental/`
- `/ai-appointment-scheduling/`
- `/ai-customer-service/`
- `/ai-sales-outreach/`

Set `VITE_SITE_URL` and `VITE_DEMO_BOOKING_URL` in the Cloudflare build environment before publishing. Sitemap and robots responses use the incoming hostname if a canonical site URL is not configured. Industry pages use distinct copy and avoid unsupported compliance, provider, or outcome claims.

No public lead form is enabled. The backend currently has no anonymous lead endpoint. Add that endpoint, server-side Turnstile verification, and rate limiting before collecting public submissions.

## Cloudflare and Railway

From this directory, `npm run check` typechecks, builds, and dry-runs the Worker; `npm run deploy` builds and deploys. Add the exact frontend origin to Railway CORS only if the browser calls Railway directly. This app proxies workspace API calls through the Worker, so browser requests are same-origin. The Worker never connects to PostgreSQL.
