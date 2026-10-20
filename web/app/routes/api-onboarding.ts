import type { Route } from "./+types/api-onboarding";
import { readSessionApiKey } from "../.server/session";

const API_BASE = (import.meta.env.VITE_PLATFORM_API_URL || "").replace(/\/$/, "");

function matchesOrigin(request: Request) {
  const origin = request.headers.get("Origin");
  return !!origin && origin === new URL(request.url).origin;
}

function allowedRequest(path: unknown, method: unknown): path is string {
  if (typeof path !== "string" || typeof method !== "string") return false;
  if (method === "GET") {
    return path === "/verticals"
      || /^\/verticals\/[a-z0-9_-]+\/template$/.test(path)
      || /^\/businesses\/[a-f0-9-]+\/onboarding\/health$/.test(path);
  }
  return (method === "POST" && path === "/onboarding/businesses")
    || (method === "POST" && /^\/businesses\/[a-f0-9-]+\/onboarding\/go-live$/.test(path));
}

export async function action({ request, context }: Route.ActionArgs) {
  if (request.method !== "POST") {
    return Response.json({ detail: "Use POST for onboarding requests." }, { status: 405 });
  }
  if (!matchesOrigin(request)) {
    return Response.json({ detail: "Request origin could not be verified." }, { status: 403 });
  }
  const apiKey = await readSessionApiKey(request, context.cloudflare.env.SESSION_ENCRYPTION_KEY);
  if (!apiKey) {
    return Response.json({ detail: "Your workspace session has expired. Sign in again." }, { status: 401 });
  }

  let input: { path?: unknown; method?: unknown; body?: unknown };
  try {
    const parsed: unknown = await request.json();
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return Response.json({ detail: "Invalid onboarding request." }, { status: 400 });
    }
    input = parsed as { path?: unknown; method?: unknown; body?: unknown };
  } catch {
    return Response.json({ detail: "Invalid onboarding request." }, { status: 400 });
  }
  if (!allowedRequest(input.path, input.method)) {
    return Response.json({ detail: "This onboarding operation is not available." }, { status: 400 });
  }
  if (!API_BASE) {
    return Response.json({ detail: "The platform API is not configured for this environment." }, { status: 503 });
  }

  try {
    const response = await fetch(API_BASE + input.path, {
      method: String(input.method),
      headers: {
        Authorization: `Bearer ${apiKey}`,
        Accept: "application/json",
        ...(input.body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(input.body === undefined ? {} : { body: JSON.stringify(input.body) }),
      redirect: "follow",
    });
    if (response.url && new URL(response.url).origin !== new URL(API_BASE).origin) {
      return Response.json({ detail: "The platform API redirected to an unexpected host." }, { status: 502 });
    }
    return new Response(await response.arrayBuffer(), {
      status: response.status,
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Type": response.headers.get("Content-Type") || "application/json",
      },
    });
  } catch {
    return Response.json({ detail: "The platform API request could not be completed." }, {
      status: 502,
      headers: { "Cache-Control": "private, no-store" },
    });
  }
}
