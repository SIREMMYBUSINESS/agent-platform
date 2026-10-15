import { SeoLandingPage, seoMeta } from "../seo-content";
import type { SeoPage } from "../seo-content";

const page: SeoPage = {
  "key": "sales",
  "path": "/ai-sales-outreach/",
  "title": "AI Sales Outreach Automation | Agent Platform",
  "description": "Coordinate configurable, policy-aware sales follow-up workflows while keeping consent, suppression, and staff review in view.",
  "eyebrow": "Sales outreach",
  "headline": "Make follow-up more consistent and easier to manage.",
  "intro": "Use configured outreach workflows to organize follow-up and keep the relevant policy checks in the process. Your team controls the message, audience, and provider setup.",
  "features": [
    [
      "Structure follow-up",
      "Configure workflows around the outreach steps your team uses."
    ],
    [
      "Respect policy checks",
      "Keep consent and suppression rules in view before outreach is sent."
    ],
    [
      "Review delivery readiness",
      "Validate provider connections and workflow behavior before using a live audience."
    ]
  ],
  "steps": [
    "Define the audience and purpose of each workflow.",
    "Review consent, suppression, and message requirements for your use case.",
    "Connect providers and validate delivery with a controlled test."
  ],
  "faqs": [
    [
      "Does the platform automatically send messages?",
      "Sending depends on your configured workflow, policy checks, and provider connections. Validate those pieces before live use."
    ],
    [
      "Can I use it across regions?",
      "The application has region-aware settings, but multi-region production routing is not yet enabled. Confirm data-location needs before onboarding customers."
    ]
  ]
};

export function meta() {
  return seoMeta(page);
}

export default function SeoRoute() {
  return <SeoLandingPage page={page} />;
}
