import { MarketingHome } from "../seo-content";

export function meta() {
  return [
    { title: "AI Receptionist & Business Agents | Agent Platform" },
    { name: "description", content: "Configure AI receptionist, appointment scheduling, customer service, and outreach workflows around your business." },
    { property: "og:type", content: "website" },
  ];
}

export default function MarketingRoute() {
  return <MarketingHome />;
}
