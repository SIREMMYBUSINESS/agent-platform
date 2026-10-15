# Agent Platform web workspace

The frontend uses React Router with Cloudflare's Vite plugin and runs on Cloudflare Workers. The FastAPI API and PostgreSQL database stay on Railway.

## Local development

From this directory, create a local environment file and run the Workers-compatible dev server:

```powershell
Copy-Item .env.example .env
npm install
npm run dev
```

The example file points to the local API at `http://127.0.0.1:8000`. Set `VITE_PLATFORM_API_URL` to the API origin before building. This value is public and is embedded in the frontend bundle.

Open `http://localhost:5173/dashboard`. Enter an API key provisioned through a trusted administrative path. The UI keeps the key in page memory only; it is not written to localStorage, cookies, or logs.

## Railway API connection

Set `VITE_PLATFORM_API_URL` to the Railway API's HTTPS origin in the Cloudflare Workers Builds environment for each deployment. Add each exact frontend HTTPS origin to Railway's `CORS_ALLOWED_ORIGINS` setting, including staging and production origins. The dashboard calls `GET /leads` and `GET /conversations`; FastAPI derives tenant scope from the verified principal. The browser never sends a tenant ID.

API keys must be provisioned out of band with `lead_reader` and conversation read access. The API currently has no browser login/session endpoint or key-recovery flow. This first workspace uses a manually provisioned key for authenticated access. Provider credentials belong in protected deployment configuration and must never be entered into this UI.

## Cloudflare deployment

The Workers runtime configuration is in `wrangler.json`. From this directory:

```powershell
npm run check
npm run deploy
```

Cloudflare hosts the React Router web app. Railway continues to host FastAPI, the worker process, and PostgreSQL. No database connection string or database credentials are sent to the frontend.
