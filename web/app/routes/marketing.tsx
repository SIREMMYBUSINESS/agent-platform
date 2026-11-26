import { MarketingHome } from "../seo-content";

export function meta() {
  const base = (import.meta.env.VITE_SITE_URL || "").replace(/\\/+$/, "");
  return [
    { title: "AI Receptionist & Business Agents | Agent Platform" },
    { name: "description", content: "Configure AI receptionist, appointment scheduling, customer service, and outreach workflows around your business." },
    { property: "og:type", content: "website" },
    ...(base ? [{ tagName: "link", rel: "canonical", href: base + "/" }] : []),
  ];
}

export default function MarketingRoute() {
  return <MarketingHome />;
}
