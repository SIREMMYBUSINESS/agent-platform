# voice-worker

Cloudflare Worker + Durable Object AI voice receptionist.
Twilio Media Streams -> Worker (edge router) -> `ReceptionistAgent` (Agents SDK Durable Object, one per call)
-> Workers AI (STT, LLM, TTS). Tool calls (booking, messages, transfer, call-log postback) are **off** in this scaffold.

## Layout
- `src/index.ts` - routes: `POST /voice/incoming` (signature-checked TwiML), `WS /voice/stream/<callSid>/<exp>/<token>` (token-checked before the DO is touched), `GET /health`
- `src/twilio.ts` - Twilio signature validation, per-call stream token, XML escaping
- `src/agent.ts` - per-call agent: STT socket, streaming LLM -> sentence-level TTS, barge-in, max-call alarm, TTS-failure fallback
- `test/twilio.test.ts` - signature and token unit tests

## Setup
1. Set `PUBLIC_ORIGIN` in `wrangler.jsonc` to your public origin (custom domain recommended), no trailing slash.
2. Secrets (Cloudflare dashboard -> Worker -> Settings -> Variables and Secrets, or `wrangler secret put`):
   `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `STREAM_SECRET` (any long random string).
3. Workers Builds: connect this repo, root directory `voice-worker`.
4. Twilio number -> Voice webhook: `POST https://<PUBLIC_ORIGIN host>/voice/incoming`.

## Verified in CI (see `.github/workflows/voice-worker.yml`)
`npm ci`, `npm run typecheck`, `npm test`, `wrangler deploy --dry-run` (Durable Object + AI bindings resolve).

## Still to verify on a deployed Worker (cannot be checked in CI)
- STT/TTS parameters at the top of `src/agent.ts` against the live Workers AI model schemas (`STT_PARAMS`, `TTS_PARAMS`).
- Agents SDK WebSocket hand-off from the Worker (`getAgentByName(...).fetch(req)`) with a real Twilio stream.
- Real-call signature validation (if it fails behind a proxy, the signed URL must equal what Twilio calls).
- First-audio latency, barge-in, max-call alarm (15 min), clean close, no leaked sockets.
- Emergency wording in `SYSTEM_PROMPT` reviewed; recording/transcription consent configured in Twilio.
- Transcript retention: decide, then implement the TODO in `finalize()`.

## Before enabling tools
Platform endpoints must be tenant-scoped and authorized; resolve the tenant server-side from the dialed number
(`state.to`), never from anything the caller says or supplies; API key stays a Worker secret.
