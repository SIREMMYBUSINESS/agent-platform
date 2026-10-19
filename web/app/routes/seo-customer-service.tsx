import { SeoLandingPage, seoMeta } from "../seo-content";
import type { SeoPage } from "../seo-content";

const page: SeoPage = {
  "key": "customerService",
  "path": "/ai-customer-service/",
  "title": "AI Customer Service Agent | Agent Platform",
  "description": "Configure a customer-service agent to handle common enquiries, collect context, and route conversations to your team when needed.",
  "eyebrow": "Customer service",
  "headline": "Give every customer a useful first response.",
  "intro": "Set up a consistent first line for routine questions, with clear boundaries for staff follow-up. Shape the workflow around the information your team needs to resolve an enquiry.",
  "features": [
    [
      "Handle configured FAQs",
      "Set responses for the questions your team receives most often."
    ],
    [
      "Gather context up front",
      "Use intake prompts to capture the details needed for follow-up."
    ],
    [
      "Escalate appropriately",
      "Define when the conversation should be handed to a staff member."
    ]
  ],
  "steps": [
    "List routine questions and the answers your team approves.",
    "Configure intake prompts, hours, and handoff points.",
    "Test common and edge-case conversations with your team."
  ],
  "faqs": [
    [
      "Can I review the responses before launch?",
      "Yes. Configure and review the workflow, then use setup checks before bringing it online."
    ],
    [
      "Does it replace my support team?",
      "It supports routine first responses and information gathering; staff remain important for exceptions and complex cases."
    ]
  ]
};

export function meta() {
  return seoMeta(page);
}

export default function SeoRoute() {
  return <SeoLandingPage page={page} />;
}
