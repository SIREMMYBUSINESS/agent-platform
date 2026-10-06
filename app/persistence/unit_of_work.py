"""Application-owned transaction and session lifecycle."""

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlmodel import Session
from app.persistence.auth_repositories import SQLApiKeyRepository, SQLApiKeyGrantRepository
from app.persistence.queue_repositories import SQLQueueTaskRepository
from app.persistence.conversation_repositories import SQLConversationAssignmentRepository
from app.persistence.workflow_repositories import SQLWorkflowExecutionRepository, SQLFollowUpRepository
from app.persistence.crm_repositories import SQLCRMContactRepository, SQLCRMSyncRepository
from app.persistence.provider_repositories import (
    SQLProviderInteractionRepository, SQLProviderWebhookRepository,
)

from app.persistence.repositories import (
    SQLAppointmentRepository,
    SQLAppointmentSlotRepository,
    SQLBusinessRepository,
    SQLContactRepository,
    SQLConversationRepository,
    SQLEventRepository,
    SQLMessageRepository,
    SQLRoutingRuleRepository,
    SQLEscalationRepository,
    SQLServiceRepository,
    SQLStaffRepository,
    SQLTenantRepository,
    SQLWorkflowRepository,
)
from app.persistence.lead_repositories import (
    SQLLeadEnrichmentRepository,
    SQLLeadEventRepository,
    SQLLeadRepository,
    SQLLeadScoreRepository,
    SQLLeadSourceRepository,
    SQLLeadTransitionRepository,
)
from app.persistence.campaign_repositories import (
    SQLCampaignEnrollmentRepository,
    SQLCampaignEventRepository,
    SQLCampaignRepository,
    SQLCampaignSequenceRepository,
    SQLCampaignStepRepository,
    SQLCampaignStepRunRepository,
    SQLOutreachMessageRepository,
)
from app.persistence.reply_repositories import (
    SQLCampaignAnalyticsRepository, SQLCampaignReplyEnrollmentRepository,
    SQLCampaignReplyRepository, SQLContactChannelConsentRepository,
)
from app.persistence.policy_repositories import (
    SQLConsentRecordRepository, SQLCountryPolicyRepository,
    SQLPolicyDecisionRepository, SQLPolicyProfileRepository,
    SQLSuppressionRepository,
)


class UnitOfWork:
    """Own one session and transaction for one application operation.

    A normal context exit commits. An exceptional exit rolls back. The session
    is closed after either path. Repositories added later must use ``session``
    and leave commit/rollback to this unit of work.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._entered = False
        self._finished = False
        self._closed = False
        self.api_keys: SQLApiKeyRepository | None = None
        self.api_key_grants: SQLApiKeyGrantRepository | None = None
        self.queue_tasks: SQLQueueTaskRepository | None = None
        self.conversation_assignments: SQLConversationAssignmentRepository | None = None
        self.workflow_executions: SQLWorkflowExecutionRepository | None = None
        self.follow_ups: SQLFollowUpRepository | None = None
        self.crm_sync: SQLCRMSyncRepository | None = None
        self.crm_contacts: SQLCRMContactRepository | None = None
        self.provider_webhooks: SQLProviderWebhookRepository | None = None
        self.provider_interactions: SQLProviderInteractionRepository | None = None
        self.tenants: SQLTenantRepository | None = None
        self.businesses: SQLBusinessRepository | None = None
        self.contacts: SQLContactRepository | None = None
        self.conversations: SQLConversationRepository | None = None
        self.messages: SQLMessageRepository | None = None
        self.workflows: SQLWorkflowRepository | None = None
        self.events: SQLEventRepository | None = None
        self.appointments: SQLAppointmentRepository | None = None
        self.appointment_slots: SQLAppointmentSlotRepository | None = None
        self.escalations: SQLEscalationRepository | None = None
        self.routing_rules: SQLRoutingRuleRepository | None = None
        self.services: SQLServiceRepository | None = None
        self.staff: SQLStaffRepository | None = None
        self.lead_sources: SQLLeadSourceRepository | None = None
        self.leads: SQLLeadRepository | None = None
        self.lead_enrichments: SQLLeadEnrichmentRepository | None = None
        self.lead_scores: SQLLeadScoreRepository | None = None
        self.lead_transitions: SQLLeadTransitionRepository | None = None
        self.lead_events: SQLLeadEventRepository | None = None
        self.campaigns: SQLCampaignRepository | None = None
        self.campaign_sequences: SQLCampaignSequenceRepository | None = None
        self.campaign_steps: SQLCampaignStepRepository | None = None
        self.campaign_enrollments: SQLCampaignEnrollmentRepository | None = None
        self.campaign_step_runs: SQLCampaignStepRunRepository | None = None
        self.campaign_events: SQLCampaignEventRepository | None = None
        self.outreach_messages: SQLOutreachMessageRepository | None = None
        self.contact_channel_consents: SQLContactChannelConsentRepository | None = None
        self.campaign_replies: SQLCampaignReplyRepository | None = None
        self.campaign_reply_enrollments: SQLCampaignReplyEnrollmentRepository | None = None
        self.campaign_analytics: SQLCampaignAnalyticsRepository | None = None
        self.consent_records: SQLConsentRecordRepository | None = None
        self.country_policies: SQLCountryPolicyRepository | None = None
        self.policy_profiles: SQLPolicyProfileRepository | None = None
        self.suppressions: SQLSuppressionRepository | None = None
        self.policy_decisions: SQLPolicyDecisionRepository | None = None

    @property
    def session(self) -> Session:
        if self._session is None or self._closed:
            raise RuntimeError("UnitOfWork session is only available inside its context")
        return self._session

    def __enter__(self) -> Self:
        if self._entered:
            raise RuntimeError("UnitOfWork cannot be entered more than once")
        self._entered = True
        self._session = self._session_factory()
        try:
            self._session.begin()
            self.api_keys = SQLApiKeyRepository(self._session)
            self.api_key_grants = SQLApiKeyGrantRepository(self._session)
            self.queue_tasks = SQLQueueTaskRepository(self._session)
            self.conversation_assignments = SQLConversationAssignmentRepository(self._session)
            self.workflow_executions = SQLWorkflowExecutionRepository(self._session)
            self.follow_ups = SQLFollowUpRepository(self._session)
            self.crm_sync = SQLCRMSyncRepository(self._session)
            self.crm_contacts = SQLCRMContactRepository(self._session)
            self.provider_webhooks = SQLProviderWebhookRepository(self._session)
            self.provider_interactions = SQLProviderInteractionRepository(self._session)
            self.tenants = SQLTenantRepository(self._session)
            self.businesses = SQLBusinessRepository(self._session)
            self.contacts = SQLContactRepository(self._session)
            self.conversations = SQLConversationRepository(self._session)
            self.messages = SQLMessageRepository(self._session)
            self.workflows = SQLWorkflowRepository(self._session)
            self.events = SQLEventRepository(self._session)
            self.appointments = SQLAppointmentRepository(self._session)
            self.appointment_slots = SQLAppointmentSlotRepository(self._session)
            self.escalations = SQLEscalationRepository(self._session)
            self.routing_rules = SQLRoutingRuleRepository(self._session)
            self.services = SQLServiceRepository(self._session)
            self.staff = SQLStaffRepository(self._session)
            self.lead_sources = SQLLeadSourceRepository(self._session)
            self.leads = SQLLeadRepository(self._session)
            self.lead_enrichments = SQLLeadEnrichmentRepository(self._session)
            self.lead_scores = SQLLeadScoreRepository(self._session)
            self.lead_transitions = SQLLeadTransitionRepository(self._session)
            self.lead_events = SQLLeadEventRepository(self._session)
            self.campaigns = SQLCampaignRepository(self._session)
            self.campaign_sequences = SQLCampaignSequenceRepository(self._session)
            self.campaign_steps = SQLCampaignStepRepository(self._session)
            self.campaign_enrollments = SQLCampaignEnrollmentRepository(self._session)
            self.campaign_step_runs = SQLCampaignStepRunRepository(self._session)
            self.campaign_events = SQLCampaignEventRepository(self._session)
            self.outreach_messages = SQLOutreachMessageRepository(self._session)
            self.contact_channel_consents = SQLContactChannelConsentRepository(self._session)
            self.campaign_replies = SQLCampaignReplyRepository(self._session)
            self.campaign_reply_enrollments = SQLCampaignReplyEnrollmentRepository(self._session)
            self.campaign_analytics = SQLCampaignAnalyticsRepository(self._session)
            self.consent_records = SQLConsentRecordRepository(self._session)
            self.country_policies = SQLCountryPolicyRepository(self._session)
            self.policy_profiles = SQLPolicyProfileRepository(self._session)
            self.suppressions = SQLSuppressionRepository(self._session)
            self.policy_decisions = SQLPolicyDecisionRepository(self._session)
        except BaseException:
            self.close()
            raise
        return self

    def commit(self) -> None:
        """Commit the active transaction; repositories must not call this."""
        if self._finished:
            return
        session = self.session
        try:
            session.commit()
        except BaseException:
            try:
                session.rollback()
            finally:
                self._finished = True
            raise
        self._finished = True

    def rollback(self) -> None:
        """Roll back the active transaction."""
        if self._finished:
            return
        try:
            self.session.rollback()
        finally:
            self._finished = True

    def close(self) -> None:
        """Close this unit's session exactly once."""
        if self._closed:
            return
        self._closed = True
        if self._session is not None:
            self._session.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            if not self._finished:
                if exc_type is None:
                    self.commit()
                else:
                    self.rollback()
        finally:
            self.close()
        return False
