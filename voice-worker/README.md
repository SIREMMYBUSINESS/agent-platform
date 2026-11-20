# Cloudflare voice receptionist

This is a separate Worker from `web/`: it owns Twilio's form webhook and media WebSocket. `ReceptionistAgent` is one SQLite-backed Durable Object per Twilio CallSid.

## Staging configuration

Wrangler's canonical configuration is `wrangler.jsonc`. Keep only this Wrangler configuration file in this directory so a stale JSON file cannot carry old variable names or web-worker URLs into a deploy.

The Worker expects these non-secret variables:

- `PUBLIC_ORIGIN`: exact HTTPS origin assigned to **this Voice Worker**, without a trailing slash. It must not point at the website Worker.
- `PLATFORM_API_URL`: staging FastAPI origin, without a path or trailing slash.

The Worker expects exactly these runtime secrets, provisioned in Cloudflare for the staging Worker (never committed):

- `TWILIO_AUTH_TOKEN`: Twilio account Auth Token used to validate incoming webhook signatures.
- `PLATFORM_API_KEY`: dedicated platform-wide staging bearer token. Its value must be identical to the staging backend's `VOICE_NUMBER_LOOKUP_TOKEN`; it is not a normal tenant API key.
- `STREAM_SECRET`: independently generated high-entropy secret used to sign short-lived, tenant-bound media-stream tokens.

The backend's `GET /voice/resolve-number` endpoint validates `VOICE_NUMBER_LOOKUP_TOKEN` and returns a mapping only for a configured E.164 number. If lookup fails, is ambiguous, or returns invalid data, the Worker must fail closed and must not connect the call.

## Local checks

From this directory:

```sh
npm ci
npm run typecheck
npm test
npx wrangler deploy --dry-run
```

A dry run does not deploy the Worker. A real staging deployment must be explicitly initiated through the staging-only deployment workflow after its Cloudflare credentials, runtime secrets, public origin and backend token have been verified.

## Incoming call flow

Configure the Twilio phone number's incoming-call webhook to `https://<voice-worker-host>/voice/incoming` with HTTP POST only after the Worker is deployed and tested. The webhook verifies Twilio's HMAC-SHA1 signature against the configured canonical public URL, returns escaped TwiML, and signs the CallSid media URL with an HMAC token. The WebSocket handler verifies that token before routing the socket to the per-call Durable Object.

## Current scope and known integration boundary

The Worker scaffold accepts Twilio Media Streams messages, forwards 8 kHz μ-law frames to Workers AI Nova-3, generates replies with Llama 3.3, generates μ-law audio with Aura, stores a local transcript in Durable Object SQLite, and ends a call at the configured maximum duration. It does not yet call the Agent Platform booking/message/transfer APIs. Do not configure or advertise those tools until matching tenant-authorized backend contracts are implemented.

The current path uses completed-utterance recognition and whole-response TTS, not sentence-level token streaming. Verify model schemas, stream pacing, interruption/barge-in, audio levels, consent, retention, tenant residency, emergency escalation and call logging with a real test call before production.
