import { test } from "node:test";
import assert from "node:assert/strict";
import { cancelHangupSchedule } from "../src/schedule";

test("no stored hangup alarm means nothing is cancelled", async () => {
  const calls: string[] = [];
  const errors: unknown[] = [];
  const cancelled = await cancelHangupSchedule(
    undefined,
    async (id) => { calls.push(id); },
    (error) => { errors.push(error); },
  );
  assert.equal(cancelled, false);
  assert.deepEqual(calls, []);
  assert.deepEqual(errors, []);
});

test("the stored hangup alarm is cancelled by its exact id when a call ends", async () => {
  const calls: string[] = [];
  const cancelled = await cancelHangupSchedule(
    "sched-123",
    async (id) => { calls.push(id); },
    () => assert.fail("no error expected"),
  );
  assert.equal(cancelled, true);
  assert.deepEqual(calls, ["sched-123"]);
});

test("a failed cancel is reported once and never throws", async () => {
  const errors: unknown[] = [];
  const boom = new Error("alarm already executed");
  const cancelled = await cancelHangupSchedule(
    "sched-9",
    async () => { throw boom; },
    (error) => { errors.push(error); },
  );
  assert.equal(cancelled, false);
  assert.equal(errors.length, 1);
  assert.equal(errors[0], boom);
});

test("a throwing error reporter cannot interrupt call finalization", async () => {
  const boom = new Error("alarm cancellation failed");
  const cancelled = await cancelHangupSchedule(
    "sched-10",
    async () => { throw boom; },
    () => { throw new Error("logger failed"); },
  );
  assert.equal(cancelled, false);
});
