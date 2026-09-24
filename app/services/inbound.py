"""Compatibility imports for the inbound application service."""

from app.services.intake_service import (
    InboundProvisioningError,
    InboundTenantMismatchError,
    IntakeService,
)

InboundService = IntakeService

__all__ = [
    "InboundProvisioningError",
    "InboundService",
    "InboundTenantMismatchError",
    "IntakeService",
]
