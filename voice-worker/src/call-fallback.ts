export interface TwilioFallbackInput {
  accountSid: string;
  callSid: string;
  authToken: string;
}

const ACCOUNT_SID_RE = /^AC[0-9a-f]{32}$/i;
const CALL_SID_RE = /^CA[0-9a-f]{32}$/i;
const FALLBACK_TWIML =
  '<?xml version="1.0" encoding="UTF-8"?><Response><Say>We are having trouble understanding the call right now. Please try again later.</Say><Hangup/></Response>';

/** Replace the active Media Stream call flow with provider-generated TwiML audio. */
export async function redirectCallToFallback(
  input: TwilioFallbackInput,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  if (!ACCOUNT_SID_RE.test(input.accountSid) || !CALL_SID_RE.test(input.callSid) || !input.authToken) {
    throw new Error("Twilio fallback configuration is invalid");
  }

  const url = `https://api.twilio.com/2010-04-01/Accounts/${input.accountSid}/Calls/${input.callSid}.json`;
  const body = new URLSearchParams({ Twiml: FALLBACK_TWIML });
  const response = await fetcher(url, {
    method: "POST",
    headers: {
      Authorization: `Basic ${btoa(`${input.accountSid}:${input.authToken}`)}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });
  if (!response.ok) {
    throw new Error(`Twilio fallback call update failed (HTTP ${response.status})`);
  }
}
