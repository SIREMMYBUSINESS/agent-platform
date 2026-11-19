// After deploy: confirm the required secrets exist and the public routes enforce their security.
// Usage: node scripts/post-deploy-check.mjs   (needs CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID)
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";

const REQUIRED_SECRETS = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "STREAM_SECRET", "PLATFORM_API_KEY"];
const origin = readFileSync("wrangler.jsonc", "utf8").match(/"PUBLIC_ORIGIN"\s*:\s*"([^"]+)"/)?.[1];
const problems = [];

if (process.env.SKIP_SECRET_CHECK === "1") {
  console.log("skip secrets check (SKIP_SECRET_CHECK=1)");
} else try {
  const out = execFileSync("npx", ["wrangler", "secret", "list", "--format", "json"], { encoding: "utf8" });
  const have = new Set(JSON.parse(out.slice(out.indexOf("["))).map((s) => s.name));
  const missing = REQUIRED_SECRETS.filter((n) => !have.has(n));
  if (missing.length) problems.push(`missing Worker secrets: ${missing.join(", ")} (Cloudflare -> voice-worker -> Settings -> Variables and Secrets)`);
} catch (e) {
  problems.push(`could not list Worker secrets: ${String(e.message).split("\n")[0]}`);
}

async function expect(label, url, init, status) {
  let last = "no response";
  for (let i = 0; i < 12; i++) {           // new custom domains can take a minute to start answering
    try {
      const res = await fetch(url, { redirect: "manual", ...init });
      if (res.status === status) return console.log(`ok   ${label} -> ${status}`);
      last = `HTTP ${res.status}`;
    } catch (e) { last = String(e.cause?.code ?? e.message); }
    await new Promise((r) => setTimeout(r, 5000));
  }
  problems.push(`${label}: expected ${status}, got ${last}`);
}

await expect("GET  /health", `${origin}/health`, {}, 200);
const streamPath = `/voice/stream/CA${"0".repeat(32)}/1900000000/${"0".repeat(64)}`;
await expect("POST /voice/incoming without a valid Twilio signature", `${origin}/voice/incoming`, {
  method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: "CallSid=CA0&To=%2B15550001111",
}, 403);
await expect("GET  stream URL without a WebSocket upgrade", `${origin}${streamPath}`, {}, 426);
// fetch() refuses Upgrade headers, so use the raw client: a bad token must get a plain 403, never a 101.
function upgradeStatus(url) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith("https:") ? https : http;
    const req = lib.request(url, {
      headers: { Connection: "Upgrade", Upgrade: "websocket", "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==" },
    });
    req.on("response", (res) => { res.resume(); resolve(res.statusCode); });
    req.on("upgrade", (_res, socket) => { socket.destroy(); resolve(101); });
    req.on("error", reject);
    req.setTimeout(8000, () => req.destroy(new Error("timeout")));
    req.end();
  });
}
{
  let last = "no response";
  for (let i = 0; i < 12; i++) {
    try {
      const status = await upgradeStatus(`${origin}${streamPath}`);
      if (status === 403) { console.log("ok   WebSocket upgrade with a bad token -> 403"); last = ""; break; }
      last = `HTTP ${status}`;
    } catch (e) { last = String(e.code ?? e.message); }
    await new Promise((r) => setTimeout(r, 5000));
  }
  if (last) problems.push(`WebSocket upgrade with a bad token: expected 403, got ${last}`);
}

if (problems.length) {
  for (const p of problems) console.error(`::error::${p}`);
  process.exit(1);
}
console.log("post-deploy checks passed");
