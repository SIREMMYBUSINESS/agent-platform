// SESSION_ENCRYPTION_KEY is configured with `wrangler secret put`, not in vars.
// Keep its binding type here so generated Worker types stay aligned with runtime.
declare namespace Cloudflare {
  interface Env {
    SESSION_ENCRYPTION_KEY: string;
  }
}

interface Env {
  SESSION_ENCRYPTION_KEY: string;
}
