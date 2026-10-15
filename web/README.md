# Agent Platform web workspace

This is the existing Remix/Vite frontend. The workspace dashboard at `/dashboard` reads the authenticated tenant's recent leads and conversations from the FastAPI service. The existing root route remains the dental onboarding flow.

## Local development

From this directory:

```powershell
npm install
$env:VITE_PLATFORM_API_URL = "http://127.0.0.1:8000"
npm run dev
```

Open `http://localhost:3000/dashboard`. Enter an API key provisioned through a trusted administrative path. The UI keeps the key in page memory only; it is not written to localStorage, cookies, or logs.

## Production connection

Set `VITE_PLATFORM_API_URL` to the Railway API's HTTPS origin when building the frontend. Add the deployed frontend's exact HTTPS origin to the backend's `CORS_ALLOWED_ORIGINS` setting. Rebuild after changing Vite environment values.

API keys must be provisioned out of band with the required grants. The dashboard calls `GET /leads` and `GET /conversations`; the authenticated principal supplies tenant scope. The browser never sends a tenant ID. Read access requires the key to have `lead_reader` and conversation read access.

The API currently has no browser login/session endpoint or key-recovery flow. Do not place provider credentials in this app. Provider setup remains part of protected deployment configuration and the existing onboarding checks.

## Deployment note

The current package runs Remix on the Node runtime. It has no Cloudflare Workers adapter/configuration yet. The dashboard can be validated locally against the Railway API; deployment to Cloudflare Workers requires a separate framework/runtime setup decision. Cloudflare can also proxy a Node frontend hosted on Railway, but that would not make the frontend an edge Worker.
