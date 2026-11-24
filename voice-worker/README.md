# Cloudflare voice receptionist

This is a separate Worker from `web/`: it owns Twilio's form webhook and media WebSocket while the existing React Router Worker continues to serve the website. `ReceptionistAgent` is one SQLite-backed Durable Object per Twilio CallSid.

## Configure and deploy

1. Set `PUBLIC_URL` to the exact public HTTPS origin assigned to this Worker, and set `TENANT_ID` and `BUSINESS_ID` to provisioned platform records. Never take tenant/business scope from a call or query string.
2. Configure the Worker runtime variables for realtime STT:
   - `CF_ACCOUNT_ID`: Cloudflare account ID.
   - `AI_GATEWAY_NAME`: the AI Gateway slug that will route to Workers AI.
   - `AI_GATEWAY_AUTH_TOKEN`: a secret accepted by AI Gateway for WebSocket authentication. Add this as a Worker secret, not a plain variable.
3. Add the existing secrets from `voice-worker/`:

   ```sh
   npx wrangler secret put TWILIO_AUTH_TOKEN
   npx wrangler secret put PLATFORM_API_KEY
   npx wrangler secret put AI_GATEWAY_AUTH_TOKEN
   ```

   Set `CF_ACCOUNT_ID` and `AI_GATEWAY_NAME` as plain-text Worker variables in the same staging environment. Do not put credentials in `wrangler.jsonc` or commit them.
4. Install dependencies and deploy from `voice-worker/`:

   ```sh
   npm ci
   npx wrangler deploy
   ```

5. Configure the Twilio phone number's incoming-call webhook to `https://<voice-worker-host>/voice/incoming` with HTTP POST.

The call webhook verifies Twilio's HMAC-SHA1 signature against the configured canonical public URL, returns escaped TwiML, and signs the CallSid media URL with an HMAC token. The WebSocket handler verifies that token before routing the socket to the per-call Durable Object. Keep the Worker endpoint on HTTPS/WSS.

## Realtime speech-to-text contract

The Durable Object opens Deepgram Nova-3 through the documented Cloudflare AI Gateway realtime WebSocket endpoint using the Workers `fetch()` WebSocket-upgrade contract. It does not call `AI.run()` for streaming STT. Twilio's 8 kHz μ-law audio is decoded to linear PCM16, upsampled to 16 kHz, and sent as binary frames with `encoding=linear16` and `sample_rate=16000`. Interim results and non-completed final segments are ignored; only `is_final && speech_final` results are eligible for an LLM turn, with duplicate-final suppression and serialized turn handling. Socket generations invalidate stale provider events, and call finalization closes the STT socket.

The Worker scaffold generates replies with Llama 3.3, generates μ-law audio with Aura, stores a local transcript in Durable Object SQLite, and ends a call at the configured maximum duration. It does **not** yet call the Agent Platform booking/message/transfer APIs: the current FastAPI application does not expose the `/appointments` and `/messages` paths in the sample. Do not configure or advertise those tools until matching tenant-authorized backend contracts are implemented. The API key and platform URL are reserved for that follow-up integration; no caller-provided identity is sent to the platform.

The current path uses completed-utterance recognition and whole-response TTS, not sentence-level token streaming, so it does not meet a sub-second latency target. Run the Worker CI checks and a real staging Twilio call before production use. Validate audio pacing, barge-in, stream format, and audio levels with the configured provider.

Call recording/transcription has privacy and retention implications. Configure consent, retention, tenant residency, emergency escalation, operational monitoring, and the platform call-log endpoint before production use.
