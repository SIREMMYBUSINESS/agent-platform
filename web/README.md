# Agent Platform web workspace

The frontend uses React Router with Cloudflare's Vite plugin and runs on Cloudflare Workers. The FastAPI API and PostgreSQL database stay on Railway.

## Local development

From this directory:

```powershell
npm install
npm run dev
```

The React Router dev server uses the Cloudflare Workers runtime locally. Set `PLATFORM_API_URL` in `wrangler.json` or a local Wrangler environment to the API origin. For production, configure it as a Cloudflare Worker variable for each environment, for example the Railway API's HTTPS origin.

Open `http://localhost:5173/dashboard`. Enter an API key provisioned through a trusted administrative path. The UI keeps the key in page memory only; it is not written to localStorage, cookies, or logs.

## Railway API connection

Add each exact frontend HTTPS origin to Railway's `CORS_ALLOWED_ORIGINS` setting, including staging and production origins. The dashboard calls `GET /leads` and `GET /conversations`; FastAPI derives tenant scope from the verified principal. The browser never sends a tenant ID.

API keys must be provisioned out of band with `lead_reader` and conversation read access. The API currently has no browser login/session endpoint or key-recovery flow. This first workspace uses a manually provisioned key for authenticated access. Provider credentials belong in protected deployment configuration and must never be entered into this UI.

## Cloudflare deployment

The Cloudflare Workers build/deploy configuration is in `wrangler.json`. Set `PLATFORM_API_URL` in each Cloudflare environment and deploy the Worker from this directory:

```powershell
npm run check
npm run deploy
```

Cloudflare hosts the React Router web app. Railway continues to host FastAPI, the worker process, and PostgreSQL. No database connection string or database credentials are sent to the frontend.
