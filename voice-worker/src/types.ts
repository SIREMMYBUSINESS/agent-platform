import type { ReceptionistAgent } from "./agent";

export interface Env {
  AI: Ai;
  RECEPTIONIST: DurableObjectNamespace<ReceptionistAgent>;
  PUBLIC_ORIGIN: string;
  PLATFORM_API_URL: string;
  PLATFORM_API_KEY: string;
  TWILIO_AUTH_TOKEN: string;
  STREAM_SECRET: string;\n  INCOMING_RATE_LIMITER: RateLimit;\n  NUMBER_LOOKUP_RATE_LIMITER: RateLimit;
}
