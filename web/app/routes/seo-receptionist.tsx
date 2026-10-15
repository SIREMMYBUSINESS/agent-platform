import { SeoLandingPage, seoMeta } from "../seo-content";
import type { SeoPage } from "../seo-content";

const page: SeoPage = {
  "key": "receptionist",
  "path": "/ai-receptionist/",
  "title": "AI Receptionist for Businesses | Agent Platform",
  "description": "Give callers a clear, consistent first point of contact with an AI receptionist configured around your business hours, intake questions, and staff handoff.",
  "eyebrow": "AI receptionist",
  "headline": "A better first response for every caller.",
  "intro": "Help your team handle routine enquiries with a digital front desk shaped around how your business works. Configure opening hours, intake questions, and when a conversation should reach a person.",
  "features": [
    [
      "Answer routine enquiries",
      "Set up responses around your services, common questions, and business hours."
    ],
    [
      "Collect the right details",
      "Use structured intake questions to understand what a caller needs."
    ],
    [
      "Hand off with context",
      "Set escalation points so staff can take over when a conversation needs a person."
    ]
  ],
  "steps": [
    "Describe how your business handles incoming enquiries.",
    "Configure hours, intake questions, and escalation preferences.",
    "Connect the required services and review readiness before launch."
  ],
  "faqs": [
    [
      "What does an AI receptionist do?",
      "It handles configured first-line enquiries, gathers information, and can route conversations to staff based on your setup."
    ],
    [
      "Can it fit different business types?",
      "The platform's workflows can be configured for different businesses. Review each workflow and provider setup before using it with customers."
    ]
  ]
};

export function meta() {
  return seoMeta(page);
}

export default function SeoRoute() {
  return <SeoLandingPage page={page} />;
}
