import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/marketing.tsx"),
  route("onboarding", "routes/_index.tsx"),
  route("api/onboarding", "routes/api-onboarding.ts"),
  route("dashboard", "routes/dashboard.tsx"),
  route("ai-receptionist", "routes/seo-receptionist.tsx"),
  route("ai-receptionist/real-estate", "routes/seo-real-estate.tsx"),
  route("ai-receptionist/dental", "routes/seo-dental.tsx"),
  route("ai-appointment-scheduling", "routes/seo-scheduling.tsx"),
  route("ai-customer-service", "routes/seo-customer-service.tsx"),
  route("ai-sales-outreach", "routes/seo-sales.tsx"),
  route("sitemap.xml", "routes/sitemap.ts"),
  route("robots.txt", "routes/robots.ts"),
] satisfies RouteConfig;
