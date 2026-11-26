import type { ReceptionistAgent } from "./agent";
import type { VoiceAdmission } from "./admission";

export interface Env {
  AI: Ai;
  RECEPTIONIST: DurableObjectNamespace<ReceptionistAgent>;
  VOICE_ADMISSION: DurableObjectNamespace<VoiceAdmission>;
  PUBLIC_ORIGIN: string;
  PLATFORM_API_URL: string;
  PLATFORM_API_KEY: string;
  TWILIO_AUTH_TOKEN: string;
  TWILIO_ACCOUNT_SID?: string;
  STREAM_SECRET: string;
}
