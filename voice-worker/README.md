# Cloudflare voice receptionist Worker

This standalone Worker handles Twilio incoming-call webhooks and Media Streams. It routes calls by CallSid to a SQLite-backed Durable Object. The website Worker in `web/` remains separate.

## Setup

1. Set `PUBLIC_URL` to the exact HTTPS origin Twilio calls, and `PLATFORM_API` to the platform API origin.
2. Add these as Cloudflare Worker secrets, not GitHub secrets:
   - `TWILIO_AUTH_TOKEN`
   - `STREAM_SECRET`
   - `PLATFORM_API_KEY`
3. The incoming webhook verifies Twilio's signature against the configured canonical public URL. It resolves tenant/business scope from the signed Twilio `To` number using `GET /voice/resolve-number?to=...`.
4. The platform must implement that endpoint and authenticate the service key, returning `{ "tenant_id": "...", "business_id": "..." }`. That endpoint does not currently exist in the platform API. Calls fail closed until it is implemented.
5. Install, type-check, and dry-run deploy:

   ```sh
   npm ci
   npm run typecheck
   npx --yes wrangler@4 deploy --dry-run --config wrangler.jsonc
   ```

6. Deploy, configure the Twilio phone number's Voice webhook as `https://<public-origin>/voice/incoming` using POST, and run a test call before production.

## Scope and production gates

- Booking, message-taking, transfer, and other platform mutations are intentionally disabled. Do not enable them until tenant-authorized platform contracts exist and have tests.
- The Worker stores recognized transcript text in Durable Object SQLite. Establish consent, retention, residency, and deletion policies before live customer traffic.
- Verify Nova-3/Aura model schemas and streaming behavior against the active Cloudflare model catalog. This scaffold does not guarantee sub-second latency.
- Validate real Twilio audio format, playback pacing, interruption behavior, timeout alarm, and emergency messaging on a deployed test number.
- Do not log phone numbers, transcripts, API credentials, raw webhook bodies, or caller audio.
- Use a fixed custom domain for signature validation; do not derive trust from proxy headers.
