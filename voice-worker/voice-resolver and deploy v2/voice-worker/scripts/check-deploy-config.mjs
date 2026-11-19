// Fails the deploy early when the Worker config still has placeholders or unsafe origins.
// Usage: node scripts/check-deploy-config.mjs [path/to/wrangler.jsonc]
import { readFileSync } from "node:fs";

const file = process.argv[2] ?? "wrangler.jsonc";
const text = readFileSync(file, "utf8");
const pick = (key) => text.match(new RegExp(`"${key}"\\s*:\\s*"([^"]*)"`))?.[1];

const errors = [];
const origin = (key, { forbidHostSuffix } = {}) => {
  const raw = pick(key);
  if (!raw) return void errors.push(`${key} is missing from ${file}`);
  let url;
  try { url = new URL(raw); } catch { return void errors.push(`${key} is not a valid URL`); }
  if (url.protocol !== "https:") errors.push(`${key} must be an https origin`);
  if (url.pathname !== "/" || url.search || url.hash || raw.endsWith("/")) {
    errors.push(`${key} must be a bare origin: no path, query, fragment or trailing slash`);
  }
  if (forbidHostSuffix && url.hostname.endsWith(forbidHostSuffix)) {
    errors.push(`${key} still has the placeholder host ${url.hostname}; set your real public origin`);
  }
  return url;
};

origin("PUBLIC_ORIGIN", { forbidHostSuffix: "example.com" });
origin("PLATFORM_API_URL");

if (!process.env.CLOUDFLARE_API_TOKEN) errors.push("CLOUDFLARE_API_TOKEN secret is not available to this job");
if (!process.env.CLOUDFLARE_ACCOUNT_ID) errors.push("CLOUDFLARE_ACCOUNT_ID is not set");

if (errors.length) {
  for (const e of errors) console.error(`::error::${e}`);
  process.exit(1);
}
console.log("deploy config looks sane");
