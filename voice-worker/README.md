# Cloudflare voice receptionist

This is a separate Worker from `web/`: it owns Twilio's form webhook and media WebSocket while the existing React Router Worker continues to serve the website. `ReceptionistAgent` is one SQLite-backed Durable Object per Twilio CallSid.

## Configure and deploy

1. Set `PUBLIC_URL` to the exact public HTTPS origin assigned to this Worker, and set `TENANT_ID` and `BUSINESS_ID` to provisioned platform records. Never take tenant/business scope from a call or query string.
2. Add secrets from `voice-worker/`:

   ```sh
   npx wrangler secret put TWILIO_AUTH_TOKEN
   npx wrangler secret put PLATFORM_API_KEY
   ```

3. Install dependencies and deploy from `voice-worker/`:

   ```sh
   npm install
   npx wrangler deploy
   ```

4. Configure the Twilio phone number's incoming-call webhook to `https://<voice-worker-host>/voice/incoming` with HTTP POST.

The call webhook verifies Twilio's HMAC-SHA1 signature against the configured canonical public URL, returns escaped TwiML, and signs the CallSid media URL with an HMAC token. The WebSocket handler verifies that token before routing the socket to the per-call Durable Object. Keep the Worker endpoint on HTTPS/WSS.

## Current scope and known integration boundary

The Worker scaffold accepts Twilio Media Streams messages, forwards 8 kHz μ-law frames to Workers AI Nova-3, generates replies with Llama 3.3, generates μ-law audio with Aura, stores a local transcript in Durable Object SQLite, and ends a call at the configured maximum duration. It does **not** yet call the Agent Platform booking/message/transfer APIs: the current FastAPI application does not expose the `/appointments` and `/messages` paths in the sample. Do not configure or advertise those tools until matching tenant-authorized backend contracts are implemented. The API key and platform URL are reserved for that follow-up integration; no caller-provided identity is sent to the platform.

The current path uses completed-utterance recognition and whole-response TTS, not sentence-level token streaming, so it does not meet a sub-second latency target. Model options and Workers AI websocket/raw response behavior can change; verify against the active Cloudflare model schemas and run a real Twilio test call before production. Twilio's 20 ms frames are emitted as audio bytes become available; validate pacing, stream format, interruption/barge-in, and audio levels with the configured provider.

Call recording/transcription has privacy and retention implications. Configure consent, retention, tenant residency, emergency escalation, operational monitoring, and the platform call-log endpoint before production use.
