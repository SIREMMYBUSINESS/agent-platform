export async function loader({ request }: { request: Request }) {
  const base = (import.meta.env.VITE_SITE_URL || "").replace(/\/+$/, "") || new URL(request.url).origin;
  return new Response(`User-agent: *\nAllow: /\nDisallow: /login\nDisallow: /dashboard\nDisallow: /onboarding\nDisallow: /leads\nDisallow: /conversations\nSitemap: ${base}/sitemap.xml\n`, {
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=3600" },
  });
}
