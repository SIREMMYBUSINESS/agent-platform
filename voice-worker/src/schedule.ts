// Cancels the call's hangup alarm, if one is stored. Kept free of Durable Object types so it can be unit-tested.
// A failed cancel must never block ending the call; even a failing error reporter is isolated.
export async function cancelHangupSchedule(
  scheduleId: string | undefined,
  cancel: (id: string) => Promise<unknown>,
  onError: (error: unknown) => void,
): Promise<boolean> {
  if (!scheduleId) return false;
  try {
    await cancel(scheduleId);
    return true;
  } catch (error) {
    try {
      onError(error);
    } catch {
      // Reporting is best-effort and must not interrupt call finalization.
    }
    return false;
  }
}
