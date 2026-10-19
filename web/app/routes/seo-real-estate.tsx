import { SeoLandingPage, seoMeta } from "../seo-content";
import type { SeoPage } from "../seo-content";

const page: SeoPage = {
  "key": "realEstate",
  "path": "/ai-receptionist/real-estate/",
  "title": "AI Receptionist for Real Estate | Agent Platform",
  "description": "Configure a real-estate receptionist workflow for property enquiries, viewing requests, and calls that need an agent's attention.",
  "eyebrow": "For real estate teams",
  "headline": "Keep property enquiries moving, even when agents are busy.",
  "intro": "Give buyers, renters, sellers, and property owners a consistent first response. Shape intake questions around the enquiry and route conversations to your team for follow-up.",
  "features": [
    [
      "Organize property enquiries",
      "Collect the caller's reason for contacting you and the details your team chooses to request."
    ],
    [
      "Capture viewing requests",
      "Configure intake to record preferred property or viewing details for staff follow-up."
    ],
    [
      "Bring in an agent",
      "Define the handoff moments where a person should continue the conversation."
    ]
  ],
  "steps": [
    "Choose the enquiry types your team wants to handle first.",
    "Write intake prompts and business-hour responses for your workflow.",
    "Connect staff follow-up and review the setup before launch."
  ],
  "faqs": [
    [
      "Is this a real-estate CRM?",
      "No. It is a configurable receptionist workflow; it does not replace your CRM or property listing system."
    ],
    [
      "Can it book property viewings automatically?",
      "The workflow can collect viewing preferences. Automated booking depends on your connected calendar and configuration, which should be validated before use."
    ]
  ]
};

export function meta() {
  return seoMeta(page);
}

export default function SeoRoute() {
  return <SeoLandingPage page={page} />;
}
