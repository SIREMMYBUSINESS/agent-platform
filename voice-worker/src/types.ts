import type { ReceptionistAgent } from "./agent";

export interface AdmissionNamespace {
  idFromName(name: string): DurableObjectId;
  get(id: DurableObjectId): { fetch(input: RequestInfo, init?: RequestInit): Promise<Response> };
}

export interface Env {
  AI: Ai;
  RECEPTIONIST: DurableObjectNamespace<ReceptionistAgent>;
  VOICE_ADMISSION: AdmissionNamespace;
  PUBLIC_ORIGIN: string;
  PLATFORM_API_URL: string;
  PLATFORM_API_KEY: string;
  TWILIO_AUTH_TOKEN: string;
  TWILIO_ACCOUNT_SID?: string;
  STREAM_SECRET: string;
}
