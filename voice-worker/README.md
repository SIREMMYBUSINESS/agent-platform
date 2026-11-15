# voice-worker

Cloudflare Worker + Durable Object AI voice receptionist.
Twilio Media Streams → Worker (edge router) → `ReceptionistAgent` (Agents SDK Durable Object, one per call) → Workers AI (STT, LLM, TTS). Tool calls (booking, messages, transfer, call-log postback) are **off**.

## Call flow and trust model
1. `POST /voice/incoming` validates Twilio's signature against `PUBLIC_ORIGIN` (never request Host/proxy headers).
2. The signed dialed number (`To`) is resolved through the platform. Lookup failures fail closed. The caller's number is not forwarded.
3. The Worker returns TwiML with a stream URL: `/voice/stream/<callSid>/<expiry>/<token>`. Twilio does not allow query strings in `<Stream>` URLs. The HMAC-SHA256 token binds the URL to CallSid and expiry.
4. The Worker rejects invalid/expired stream tokens before creating or waking the Durable Object. The Durable Object is keyed by CallSid.
5. The Durable Object receives only the dialed number in Stream custom parameters. It does not derive tenant authority from caller speech or caller-supplied values. Tools are disabled.

## Platform contract (not yet implemented)
The Worker expects:

```http
GET {PLATFORM_API_URL}/voice/resolve-number?to=%2B15550001111
Authorization: Bearer <service key>
```

Success response:

```json
{"tenant_id":"tenant-id","business_id":"business-id"}
```

IDs must match `^[A-Za-z0-9_-]{1,64}$`. The endpoint must authenticate a dedicated service key, resolve exact E.164 numbers from provisioned tenant/business data, return only those IDs, apply rate limits, and fail closed. Do not log the dialed number together with transcripts.

## Layout
- `src/index.ts` — router; validates stream token before handing off to the Durable Object.
- `src/incoming.ts` — webhook validation, number lookup, and TwiML generation.
- `src/twilio.ts` — signature validation, stream tokens, and XML escaping.
- `src/agent.ts` — per-call agent, STT/LLM/TTS, barge-in, max-call alarm, and transcript persistence.
- `test/` — signature, stream-token, incoming signature, lookup, and fail-closed tests.

## Setup
1. In `wrangler.jsonc`, set `PUBLIC_ORIGIN` to the exact public origin (custom domain recommended, no trailing slash) and set `PLATFORM_API_URL`.
2. Set Cloudflare Worker secrets: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `STREAM_SECRET`, and `PLATFORM_API_KEY`. Never commit secret values.
3. Connect a separate Worker through Workers Builds with root directory `voice-worker`.
4. Configure the Twilio voice webhook as `POST https://<PUBLIC_ORIGIN host>/voice/incoming`.
5. Run checks from this directory: `npm ci`, `npm run typecheck`, `npm test`, and `npm run dry-run`.

## Production checks still required
- Verify Workers AI Nova-3 and Aura parameters against the current model schemas.
- Verify Agents SDK WebSocket handoff with a real Twilio stream.
- Test canonical URL/signature behavior behind the deployed custom domain.
- Measure first-audio latency; test barge-in, the 15-minute alarm, clean close, and socket cleanup.
- Review emergency wording and configure recording/transcription consent.
- Decide transcript retention, implement the call-log/retention path, and do not log transcripts or phone numbers.
- Keep tools disabled until their platform contracts are tenant-scoped, authorized, and tested. Enable message-taking, booking, transfer, and call-log posting separately after review.
