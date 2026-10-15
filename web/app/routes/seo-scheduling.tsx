import { SeoLandingPage, seoMeta } from "../seo-content";
import type { SeoPage } from "../seo-content";

const page: SeoPage = {
  "key": "scheduling",
  "path": "/ai-appointment-scheduling/",
  "title": "AI Appointment Scheduling Agent | Agent Platform",
  "description": "Help customers make appointment requests through a configured scheduling workflow that accounts for your services, hours, and team.",
  "eyebrow": "Appointment scheduling",
  "headline": "Turn appointment questions into a clear next step.",
  "intro": "Give customers a guided way to share what they need and when. Configure services, business hours, and scheduling connections to support appointment workflows for your team.",
  "features": [
    [
      "Collect useful preferences",
      "Ask for service and timing details before staff or connected tools continue."
    ],
    [
      "Reflect your working hours",
      "Configure availability context around your business schedule and timezone."
    ],
    [
      "Connect your calendar",
      "Choose and validate calendar providers as part of setup; availability depends on your integration."
    ]
  ],
  "steps": [
    "Define the services and appointment lengths you offer.",
    "Set business hours and the local timezone.",
    "Connect a calendar and test the complete booking flow before launch."
  ],
  "faqs": [
    [
      "Does the agent guarantee a confirmed appointment?",
      "No. A confirmed booking depends on the configured calendar integration and must be tested in your environment."
    ],
    [
      "Can the workflow support multiple services?",
      "The onboarding setup can include multiple services and durations for the scheduling workflow."
    ]
  ]
};

export function meta() {
  return seoMeta(page);
}

export default function SeoRoute() {
  return <SeoLandingPage page={page} />;
}
