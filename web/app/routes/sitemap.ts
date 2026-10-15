const paths = [
  "/",
  "/ai-receptionist/",
  "/ai-receptionist/real-estate/",
  "/ai-receptionist/dental/",
  "/ai-appointment-scheduling/",
  "/ai-customer-service/",
  "/ai-sales-outreach/",
];

export async function loader({ request }: { request: Request }) {
  const configured = (import.meta.env.VITE_SITE_URL || "").replace(/\\/$/, "");
  const requestOrigin = new URL(request.url).origin;
  const base = configured || requestOrigin;
  const urls = paths.map((path) => `  <url><loc>${base}${path}</loc></url>`).join("\\n");
  return new Response(`<?xml version="1.0" encoding="UTF-8"?>\\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\\n${urls}\\n</urlset>`, {
    headers: { "Content-Type": "application/xml; charset=utf-8", "Cache-Control": "public, max-age=3600" },
  });
}
