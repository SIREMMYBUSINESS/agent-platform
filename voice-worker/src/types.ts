import type { ReceptionistAgent } from "./agent";

export interface RateLimiter {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
  AI: Ai;
  RECEPTIONIST: DurableObjectNamespace<ReceptionistAgent>;
  PUBLIC_ORIGIN: string;
  PLATFORM_API_URL: string;
  PLATFORM_API_KEY: string;
  TWILIO_AUTH_TOKEN: string;
  STREAM_SECRET: string;
  INCOMING_RATE_LIMITER: RateLimiter;
  NUMBER_LOOKUP_RATE_LIMITER: RateLimiter;
}
