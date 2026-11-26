# Cloudflare voice receptionist

This Worker owns Twilio's incoming-call webhook and media WebSocket. `ReceptionistAgent` is one SQLite-backed Durable Object per Twilio CallSid. `VoiceAdmission` is one SQLite-backed Durable Object per Twilio AccountSid.

## Staging configuration

Wrangler's canonical configuration is `wrangler.jsonc`.

- `PUBLIC_ORIGIN`: exact HTTPS origin assigned to this Voice Worker, without a trailing slash.
- `PLATFORM_API_URL`: platform FastAPI origin, without a path or trailing slash.
- `TWILIO_ACCOUNT_SID`: optional expected Twilio account SID. If configured, signed webhooks from another account are rejected.

Runtime secrets (never commit): `TWILIO_AUTH_TOKEN`, `PLATFORM_API_KEY`, and `STREAM_SECRET`.

## Inbound admission policy

After Twilio's signature is validated, the webhook delegates admission to `VoiceAdmission`, keyed by the signed `AccountSid`.

1. A durable CallSid response lookup runs first. A duplicate webhook returns the stored TwiML and consumes no additional rate-limit token or resolver lookup.
2. The account-wide rolling window permits at most 60 new CallSids in 60 seconds, across all numbers on that Twilio account.
3. A positive-only E.164-to-business mapping cache has a 30-second TTL.
4. The resolved tenant/business receives its configured `voice_calls_per_minute` rolling-window budget. The default is 10/minute and the platform allows values from 1 to 60.
5. A rejected call receives HTTP 200 TwiML with a short busy message and Hangup. No source-IP limiter is used.

Only successful number mappings are cached. Unknown numbers, invalid mappings, and platform failures are never cached. The account ceiling is checked before number resolution; the business budget is checked after the tenant/business is known.

The per-account Durable Object serializes admission decisions and stores successful webhook responses for 24 hours. Its counters are durable and shared across Worker isolates.

## Local checks

From this directory:

```sh
npm ci
npm run typecheck
npm test
npx wrangler deploy --dry-run
```

A dry run does not deploy the Worker. A real deployment must be explicitly initiated through the staging-only deployment workflow after Cloudflare credentials, Durable Object migrations, runtime secrets, public origin and backend token are verified.

## Incoming call flow

Configure Twilio's incoming-call webhook only after the Worker is deployed and tested. The webhook validates Twilio's HMAC-SHA1 signature against the configured canonical public URL, then admission creates a tenant-bound signed media-stream URL. The WebSocket handler verifies that token before routing the socket to the per-call Durable Object.
