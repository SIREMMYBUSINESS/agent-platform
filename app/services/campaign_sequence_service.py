"""Sequence creation and ordered campaign step lookup."""

from __future__ import annotations

from typing import Any
from uuid import UUID


class CampaignSequenceService:
    def create_default(self, *, uow: Any, tenant_id: str, business_id: str,
                       campaign_id: UUID, steps: list[Any]):
        sequence = uow.campaign_sequences.create(
            tenant_id=tenant_id, business_id=business_id,
            campaign_id=campaign_id, name="default", sequence_order=0,
        )
        for index, step in enumerate(steps):
            template = step.template if hasattr(step, "template") else step["template"]
            if hasattr(template, "model_dump"):
                template = template.model_dump(exclude_none=True)
            delay = step.delay_seconds if hasattr(step, "delay_seconds") else step["delay_seconds"]
            uow.campaign_steps.create(
                tenant_id=tenant_id, business_id=business_id,
                sequence_id=sequence.id, step_order=index,
                delay_seconds=delay, template_data=template,
            )
        return sequence

    def next_step(self, *, uow: Any, tenant_id: str, business_id: str,
                  sequence_id: UUID, current_step_order: int):
        steps = uow.campaign_steps.list_for_sequence(
            tenant_id=tenant_id, business_id=business_id, sequence_id=sequence_id
        )
        return next((step for step in steps if step.step_order > current_step_order), None)

    @staticmethod
    def steps_for(uow: Any, *, tenant_id: str, business_id: str, sequence_id: UUID):
        return uow.campaign_steps.list_for_sequence(
            tenant_id=tenant_id, business_id=business_id, sequence_id=sequence_id
        )


__all__ = ["CampaignSequenceService"]
