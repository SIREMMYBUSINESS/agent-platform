# special-enigma
RECEPTIONISTS 
We build a multi-agent orchestration system with a control plane and specialized workers.

High-level layout

Inbound channels

Phone / VoIP
Website chat
WhatsApp / SMS / Email
Form submissions / lead capture
Voicemail
Event intake layer

HTTP APIs
Telephony webhook adapters
Messaging adapters
CRM/webhook adapters
Orchestration layer

Event bus
Workflow engine
Routing + state machine
Retry / idempotency / DLQ handling
Agent layer

Receptionist agent
Lead qualification agent
Scheduling agent
Outreach agent
Enrichment agent
CRM sync agent
Compliance / policy agent
Escalation / human handoff agent
QA agent
Integrations

Calendars
CRM
Email / SMS / WhatsApp
Voice transcription
Search / enrichment providers
Knowledge base / docs
Legal / compliance record store
Storage layer

Postgres for structured data
Redis for queueing + session memory
Vector DB for KB/search
Object storage for transcripts/recordings
Observability

Logs
traces
metrics
replay / audit trail
Agent model
We do not build one giant “AI receptionist + outreach agent”.

We build a swarm of specialized agents.

Agent 1: Intake Router Purpose:

classify inbound contact
determine channel
determine tenant + location + business context
decide which agent is responsible
Agent 2: Receptionist Agent Purpose:

greet
ask clarifying questions
answer FAQs
detect urgency
qualify intent
book or route
summarize and hand off
Agent 3: Lead Qualification Agent Purpose:

gather lead details
score quality
identify fit
determine service type
create tasks
Agent 4: Scheduling Agent Purpose:

check calendar availability
propose times
handle reschedules
send confirmations and reminders
Agent 5: Outreach Agent Purpose:

build campaign sequences
generate personalized messages
choose channel / timing / variant
monitor replies
route replies to next action
Agent 6: Enrichment Agent Purpose:

enrich lead records from external data
validate contact details
research business context
detect missing fields
Agent 7: Compliance Agent Purpose:

check consent
check DNC / suppression / opt-out
apply region rules
block / flag unsafe content
log audit trail
Agent 8: Escalation Agent Purpose:

route urgent or complex cases to human
create tickets in Zendesk/Slack/email
ensure follow-up
Agent 9: CRM Sync Agent Purpose:

update contact records
update pipeline stage
log activity
keep data aligned
Agent 10: QA Agent Purpose:

score outbound/inbound quality
detect hallucination / policy issues
flag low quality flows
Core workflows
Workflow A: Inbound Call / Chat / Voicemail State flow:

NEW
CHANNEL_RECEIVED
INTENT_CLASSIFIED
QUALIFIED
SCHEDULE_REQUESTED
APPOINTMENT_CONFIRMED
FOLLOW_UP_SENT
RESOLVED
ESCALATED
CLOSED
Workflow B: Outbound lead campaign State flow:

LEAD_IMPORTED
ENRICHED
SCORED
COMPLIANCE_APPROVED
CAMPAIGN_PLANNED
MESSAGE_SENT
RESPONSE_RECEIVED
REPLY_CLASSIFIED
FOLLOW_UP_SENT
APPOINTMENT_BOOKED
CONVERTED
OPTED_OUT
CLOSED
Workflow C: Appointment lifecycle

proposed
pending confirmation
confirmed
reminder sent
attended
no-show
rescheduled
completed
canceled
Event-driven design
We need durable event topics such as:

inbound.contact.received
inbound.voice.transcribed
intent.detected
lead.qualified
appointment.requested
appointment.confirmed
outreach.campaign.created
outreach.message.sent
reply.received
consent.updated
policy.violation
human.escalation.created
crm.sync.requested
Each event should include:

tenant_id
business_id
location_id
channel
contact_id
conversation_id
lead_id
correlation_id
event_id
timestamp
payload
source
policy_version
This is critical for auditability and debugging.

Multi-tenant design
Each tenant has:

business profile
brand voice
service catalog
business hours
default routing logic
region
compliance policy profile
approved scripts
negative keywords / escalation rules
staff mapping
permissions
Tenant-specific configuration should be loaded into runtime context before each workflow.

Policy engine
This should be mandatory and first class.

For each outbound or inbound action, run:

region policy
business type policy
channel policy
contact consent policy
opt-out policy
DNC / suppression check
PII storage policy
human escalation rules
Examples:

EU SMS/email to a contact without consent => block
US phone campaign to DNC contact => block
healthcare intake with no escalation route => route to human
This is a legal service request => require human triage
Data model
Core entities

Tenant
Business
Location
User / Staff
Contact
Lead
Conversation
Message
Appointment
Campaign
Task
ConsentRecord
RouteRule
PolicyProfile
AuditEvent
FileAttachment
IntegrationConnection
Example schema (conceptual)

Tenant

id
name
region
default_timezone
created_at
Business

id
tenant_id
name
vertical
website_url
business_hours
policy_profile_id
default_language
region_country_code
Location

id
business_id
name
timezone
address
phone_number
whatsapp_number
Contact

id
tenant_id
business_id
full_name
phone
email
whatsapp
source
consent_status
created_at
Lead

id
tenant_id
business_id
contact_id
source_channel
lead_score
status
assigned_agent_id
created_at
Conversation

id
tenant_id
business_id
contact_id
channel
workflow_state
correlation_id
started_at
updated_at
Message

id
conversation_id
sender
channel
content
direction
provider_message_id
created_at
Appointment

id
conversation_id
lead_id
staff_id
start_time
end_time
status
timezone
created_at
Campaign

id
tenant_id
business_id
name
channel
audience_filter
status
policy_profile_id
created_at
ConsentRecord

id
tenant_id
contact_id
channel
consent_type
granted_at
source
expiry_at
opt_out_at
valid
PolicyProfile

id
tenant_id
region
business_type
restrictions_json
allowed_channels_json
consent_rules_json
AuditEvent

id
tenant_id
entity_type
entity_id
event_type
actor
payload_json
created_at
We also need a working memory model:

session_state
conversation_state
vector memory or short-term memory
tool call history
escalation record
Why async and durable workflow is necessary
This is not a synchronous chatbot.

You need:

long-lived conversations
delayed follow-ups
scheduled reminders
queued outreach
retries with backoff
human-in-the-loop
multi-step behavior
This means:

use workflow orchestration
event bus + durable queue
idempotent task execution
state tracking per conversation and lead
Recommended stack
This should be our initial stack, not overengineered:

Core app

Python
FastAPI
Pydantic
SQLAlchemy / SQLModel
PostgreSQL
Redis
Celery or RQ for async tasks
Temporal or custom workflow engine
Prometheus + Grafana
OpenTelemetry
AI layer

OpenAI or Azure OpenAI
transcription provider
embeddings / vector DB
optional local model if needed
Messaging / telephony

Twilio / Plivo / Telnyx
SendGrid / Postmark / SES
Twilio SMS / WhatsApp
Deepgram / Whisper / Azure Speech
Integrations

Google Calendar / Outlook
HubSpot / Pipedrive / Salesforce
Slack / email / ticketing
This is a good starting stack for a production MVP.

Minimal runnable skeleton (Python)
This is the first step we will actually build.

Project structure

app/
main.py
config.py
models.py
schemas.py
workflows.py
agents.py
tasks.py
policies.py
events.py
