import type { ReceptionistAgent } from "./agent";

export interface Env {
  AI: Ai;
  RECEPTIONIST: DurableObjectNamespace<ReceptionistAgent>;
  VOICE_ADMISSION: DurableObjectNamespace<any>;
  PUBLIC_ORIGIN: string;
  PLATFORM_API_URL: string;
  PLATFORM_API_KEY: string;
  TWILIO_AUTH_TOKEN: string;
  TWILIO_ACCOUNT_SID?: string;
  STREAM_SECRET: string;
}
