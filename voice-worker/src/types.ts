import type { ReceptionistAgent } from "./agent";

export interface RateLimiter {\n  limit(options: { key: string }): Promise<{ success: boolean }>;\n}\n\nexport interface Env {
  AI: Ai;
  RECEPTIONIST: DurableObjectNamespace<ReceptionistAgent>;
  PUBLIC_ORIGIN: string;
  PLATFORM_API_URL: string;
  PLATFORM_API_KEY: string;
  TWILIO_AUTH_TOKEN: string;
  STREAM_SECRET: string;\n  INCOMING_RATE_LIMITER: RateLimiter;\n  NUMBER_LOOKUP_RATE_LIMITER: RateLimiter;
}
