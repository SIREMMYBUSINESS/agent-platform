import type { ReceptionistAgent } from "./agent";

export interface Env {
  AI: Ai;
  RECEPTIONIST: DurableObjectNamespace<ReceptionistAgent>;
  PUBLIC_ORIGIN: string;
  PLATFORM_API_URL: string;
  PLATFORM_API_KEY: string;
  TWILIO_ACCOUNT_SID: string;
  TWILIO_AUTH_TOKEN: string;
  STREAM_SECRET: string;
  CF_ACCOUNT_ID: string;
  AI_GATEWAY_NAME: string;
  AI_GATEWAY_AUTH_TOKEN: string;
}
