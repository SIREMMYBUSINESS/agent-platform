export type SeoPage = {
  key: string; path: string; title: string; description: string; eyebrow: string;
  headline: string; intro: string; features: [string, string][];
  steps: string[]; faqs: [string, string][];
};

export const seoPages: SeoPage[] = [
  {
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
  },
  {
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
  },
  {
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
  },
  {
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
  },
  {
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
  },
  {
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
  }
];

export function seoMeta(page: SeoPage) {
  const base = (import.meta.env.VITE_SITE_URL || "").replace(/\/+$/, "");
  const canonical = base ? base + page.path : null;
  return [
    { title: page.title },
    { name: "description", content: page.description },
    { property: "og:type", content: "website" },
    { property: "og:title", content: page.title },
    { property: "og:description", content: page.description },
    { name: "twitter:card", content: "summary" },
    ...(canonical ? [{ tagName: "link", rel: "canonical", href: canonical }] : []),
  ];
}

export function SeoLandingPage({ page }: { page: SeoPage }) {
  const bookingUrl = import.meta.env.VITE_DEMO_BOOKING_URL || "";
  const base = (import.meta.env.VITE_SITE_URL || "").replace(/\/+$/, "");
  const schema = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "SoftwareApplication",
        name: "Agent Platform",
        applicationCategory: "BusinessApplication",
        description: page.description,
        ...(base ? { url: base + page.path } : {}),
      },
      {
        "@type": "FAQPage",
        mainEntity: page.faqs.map(([question, answer]) => ({
          "@type": "Question",
          name: question,
          acceptedAnswer: { "@type": "Answer", text: answer },
        })),
      },
    ],
  };
  const structuredData = JSON.stringify(schema).replace(/</g, "\\u003c");

  return (
    <main className="marketing-page">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: structuredData }} />
      <section className="marketing-hero">
        <div className="marketing-hero-copy">
          <p className="eyebrow">{page.eyebrow}</p>
          <h1>{page.headline}</h1>
          <p>{page.intro}</p>
          <div className="hero-actions">
            {bookingUrl ? <a className="marketing-button" href={bookingUrl}>Book a walkthrough</a> : <a className="marketing-button" href="/dashboard">Explore the workspace</a>}
            <a className="marketing-text-link" href="/onboarding">See setup flow <span aria-hidden="true">→</span></a>
          </div>
          {!bookingUrl && <p className="configuration-note">Set a demo booking URL to add a direct walkthrough link.</p>}
        </div>
        <div className="hero-visual" aria-hidden="true">
          <div className="visual-orbit orbit-one" /><div className="visual-orbit orbit-two" />
          <div className="visual-card"><span className="visual-pulse" /><div><small>FIRST RESPONSE</small><strong>How can we help?</strong><p>Gather context · Route to your team</p></div></div>
          <span className="visual-label label-top">Business hours</span><span className="visual-label label-bottom">Staff handoff</span>
        </div>
      </section>
      <section className="marketing-section" aria-labelledby="features-title">
        <div className="section-title"><p className="eyebrow">A practical front desk</p><h2 id="features-title">Configured for the way your team works.</h2><p>Start with clear workflows and verify every connection before you rely on them with customers.</p></div>
        <div className="feature-grid">{page.features.map(([title, copy], index) => <article className="feature-card" key={title}><span className="feature-index">0{index + 1}</span><h3>{title}</h3><p>{copy}</p></article>)}</div>
      </section>
      <section className="how-section" aria-labelledby="how-title">
        <div className="section-title"><p className="eyebrow">Getting started</p><h2 id="how-title">Set it up, check it, then go live.</h2></div>
        <ol className="steps-list">{page.steps.map((step, index) => <li key={step}><span>{index + 1}</span><p>{step}</p></li>)}</ol>
      </section>
      <section className="faq-section" aria-labelledby="faq-title">
        <div className="section-title"><p className="eyebrow">Common questions</p><h2 id="faq-title">What to know before getting started.</h2></div>
        <div className="faq-list">{page.faqs.map(([question, answer]) => <article className="faq-item" key={question}><h3>{question}</h3><p>{answer}</p></article>)}</div>
      </section>
      <section className="solutions-section" aria-labelledby="solutions-title">
        <div className="section-title"><p className="eyebrow">Explore the platform</p><h2 id="solutions-title">Other ways to use the agent platform.</h2></div>
        <div className="solution-links">{seoPages.filter((item) => item.path !== page.path).map((item) => <a key={item.path} href={item.path}>{item.eyebrow}<span aria-hidden="true">↗</span></a>)}</div>
      </section>
      <footer className="marketing-footer"><span>Agent Platform</span><div><a href="/onboarding">Onboarding</a><a href="/dashboard">Workspace</a></div><p>Review provider readiness, policy rules, and handoff behavior before using an agent with customers.</p></footer>
    </main>
  );
}

export function MarketingHome() {
  return (
    <main className="marketing-page">
      <section className="marketing-hero home-hero">
        <div className="marketing-hero-copy"><p className="eyebrow">AI agents for customer conversations</p><h1>Give every enquiry a thoughtful next step.</h1><p>Configure receptionist, scheduling, customer service, and outreach workflows around your business, with staff handoff and readiness checks built into setup.</p><div className="hero-actions"><a className="marketing-button" href="/ai-receptionist/">Explore AI receptionist</a><a className="marketing-text-link" href="/dashboard">Open the workspace <span aria-hidden="true">→</span></a></div></div>
        <div className="hero-visual" aria-hidden="true"><div className="visual-orbit orbit-one"/><div className="visual-orbit orbit-two"/><div className="visual-card"><span className="visual-pulse"/><div><small>AGENT PLATFORM</small><strong>Listen. Understand. Route.</strong><p>Configured around your team</p></div></div><span className="visual-label label-top">Intake</span><span className="visual-label label-bottom">Human handoff</span></div>
      </section>
      <section className="marketing-section"><div className="section-title"><p className="eyebrow">Solutions</p><h2>Start with the conversation you want to improve.</h2><p>Explore a workflow, then use onboarding to configure and validate it for your organization.</p></div><div className="solution-grid">{seoPages.map((page) => <a className="solution-card" href={page.path} key={page.path}><span>{page.eyebrow}</span><h3>{page.headline}</h3><p>{page.description}</p><strong>Explore solution <span aria-hidden="true">→</span></strong></a>)}</div></section>
      <section className="bottom-cta"><div><p className="eyebrow">Have a workflow in mind?</p><h2>See how setup works.</h2><p>Review the current onboarding flow and readiness checks.</p></div><a className="marketing-button" href="/onboarding">View onboarding</a></section>
      <footer className="marketing-footer"><span>Agent Platform</span><div><a href="/onboarding">Onboarding</a><a href="/dashboard">Workspace</a></div><p>Review provider readiness, policy rules, and handoff behavior before using an agent with customers.</p></footer>
    </main>
  );
}
