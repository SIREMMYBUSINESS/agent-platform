"""Provider-independent CRM synchronization contracts and services."""

from app.integrations.crm.base import CRMProvider
from app.integrations.crm.service import CRMIntegrationService
from app.integrations.crm.sync_engine import CRMSyncEngine
from app.integrations.crm.hubspot import HubSpotCRMProvider
from app.integrations.crm.salesforce import SalesforceCRMProvider

__all__ = ["CRMProvider", "CRMIntegrationService", "CRMSyncEngine",
           "HubSpotCRMProvider", "SalesforceCRMProvider"]
