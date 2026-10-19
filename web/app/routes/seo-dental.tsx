import { SeoLandingPage, seoMeta } from "../seo-content";
import type { SeoPage } from "../seo-content";

const page: SeoPage = {
  "key": "dental",
  "path": "/ai-receptionist/dental/",
  "title": "AI Receptionist for Dental Offices | Agent Platform",
  "description": "Configure a dental front-desk workflow for practice hours, patient intake questions, appointment requests, and staff escalation.",
  "eyebrow": "For dental practices",
  "headline": "Make the next step clear for every patient enquiry.",
  "intro": "Set up a digital front desk around your practice hours, services, and team. Collect the information your staff need and make clear when a patient should speak with the practice directly.",
  "features": [
    [
      "Use practice-specific intake",
      "Customize questions and service durations to match your practice setup."
    ],
    [
      "Support appointment requests",
      "Collect appointment preferences and connect scheduling tools where configured."
    ],
    [
      "Keep clinical decisions with people",
      "Route clinical, urgent, or uncertain questions to practice staff. The receptionist does not diagnose or provide treatment advice."
    ]
  ],
  "steps": [
    "Load the dental practice starting template.",
    "Adjust hours, services, intake questions, and staff details.",
    "Connect providers and review every readiness check before launch."
  ],
  "faqs": [
    [
      "Does it give dental or medical advice?",
      "No. Configure it to direct clinical questions to your practice team; it does not provide diagnosis or treatment advice."
    ],
    [
      "Can I customize the workflow for my practice?",
      "Yes. The current onboarding flow supports editing practice hours, intake prompts, services, messages, and staff details."
    ]
  ]
};

export function meta() {
  return seoMeta(page);
}

export default function SeoRoute() {
  return <SeoLandingPage page={page} />;
}
