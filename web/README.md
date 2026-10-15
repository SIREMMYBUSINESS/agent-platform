# Agent Platform web app

The public SEO site and tenant workspace use React Router with Cloudflare's Vite plugin on Cloudflare Workers. FastAPI and PostgreSQL remain on Railway.

## Local development

Copy `.env.example` to `.env`, create `.dev.vars` with a random 32-byte base64 `SESSION_ENCRYPTION_KEY`, then run:

```powershell
npm install
npm run dev
```

Set `VITE_PLATFORM_API_URL` in `.env` to the Railway API's HTTPS origin. Set `VITE_SITE_URL` to the canonical public site origin and `VITE_DEMO_BOOKING_URL` to the real walkthrough booking page before publishing. These are public build configuration. Never prefix secrets with `VITE_`.

## Workspace session security

The dashboard posts a provisioned tenant API key to a React Router server action. The Worker validates tenant-scoped read access against the Railway API, encrypts the key with AES-GCM, and keeps it only in an HttpOnly, Secure, SameSite=Strict cookie that expires after one hour. Server loaders proxy dashboard reads; browser JavaScript never receives the stored key and no API credential is written to localStorage. The key is encrypted but remains a tenant credential, so use appropriately limited grants and rotate/revoke it when needed.

Set `SESSION_ENCRYPTION_KEY` as a Cloudflare Worker secret (for example, with `npx wrangler secret put SESSION_ENCRYPTION_KEY`). Use a distinct key per environment. The current backend uses tenant-scoped `ap_` API keys and `lead_reader` grants; it does not expose the proposed `read:seo_pages` or `create:seo_lead_submissions` scopes.

Never put `PLATFORM_SERVICE_TOKEN` in browser code, HTML, responses, or a `VITE_*` variable. This frontend does not use a shared service token. The backend has no public SEO-page data endpoint or anonymous lead-submission endpoint yet.

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

## Cloudflare and Railway

From this directory, `npm run check` typechecks, builds, and dry-runs the Worker; `npm run deploy` builds and deploys. Add the exact frontend origin to Railway CORS only if the browser calls Railway directly. Dashboard reads proxy through the Worker, so browser requests are same-origin. The Worker never connects to PostgreSQL.
