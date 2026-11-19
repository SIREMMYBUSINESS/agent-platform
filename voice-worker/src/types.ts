import type { ReceptionistAgent } from "./agent";

export interface Env {
  AI: Ai;
  RECEPTIONIST: DurableObjectNamespace<ReceptionistAgent>;
  PUBLIC_ORIGIN: string;
  TWILIO_ACCOUNT_SID: string;
  TWILIO_AUTH_TOKEN: string;
  STREAM_SECRET: string;
  PLATFORM_API_URL?: string;
  PLATFORM_API_KEY?: string;
}
