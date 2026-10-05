"""Registered, durable receptionist workflow execution and conversation actions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.auth.principal_resolver import AuthorizationError
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.observability.metrics import metrics


class WorkflowError(Exception):
    """Base workflow execution or registration failure."""


class WorkflowNotFoundError(WorkflowError):
    """No registered handler matches this workflow name."""


class WorkflowConflictError(WorkflowError):
    """The idempotency key conflicts or execution is already in progress."""


WorkflowHandler = Callable[["WorkflowContext"], dict[str, Any]]


@dataclass(frozen=True)
class WorkflowContext:
    principal: AuthenticatedPrincipal
    uow: UnitOfWork
    conversation: Any
    input_data: dict[str, Any]
    now: datetime
    idempotency_key: str


@dataclass(frozen=True)
class WorkflowResult:
    workflow_name: str
    idempotency_key: str
    status: str
    attempts: int
    result: dict[str, Any]
    replayed: bool = False


class WorkflowRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, WorkflowHandler] = {}

    def register(self, name: str, handler: WorkflowHandler) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", name):
            raise ValueError("workflow name is invalid")
        if not callable(handler):
            raise TypeError("workflow handler must be callable")
        if name in self._handlers:
            raise ValueError("workflow is already registered")
        self._handlers[name] = handler

    def get(self, name: str) -> WorkflowHandler:
        try:
            return self._handlers[name]
        except KeyError as exc:
            raise WorkflowNotFoundError("workflow is not registered") from exc


class ReceptionistWorkflowEngine:
    def __init__(self, *, uow_factory: Callable[[], UnitOfWork],
                 registry: WorkflowRegistry | None = None,
                 now_provider: Callable[[], datetime] | None = None) -> None:
        self._uow_factory = uow_factory
        self.registry = registry or WorkflowRegistry()
        self._now = now_provider or (lambda: datetime.now(UTC))

    def register(self, name: str, handler: WorkflowHandler) -> None:
        self.registry.register(name, handler)

    def _require_principal(self, principal: AuthenticatedPrincipal) -> None:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("workflow clock must be timezone-aware")
        now = now.astimezone(UTC)
        if (not isinstance(principal, AuthenticatedPrincipal)
                or "workflow_executor" not in principal.roles
                or principal.issued_at > now
                or (principal.expires_at is not None and principal.expires_at <= now)):
            raise AuthorizationError("Workflow access denied")

    def execute(self, principal: AuthenticatedPrincipal, conversation_id: UUID, *,
                workflow_name: str, idempotency_key: str,
                input_data: dict[str, Any] | None = None) -> WorkflowResult:
        self._require_principal(principal)
        handler = self.registry.get(workflow_name)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        data = input_data or {}
        if not isinstance(data, dict):
            raise ValueError("input_data must be an object")
        try:
            encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("input_data must contain JSON values") from exc
        input_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("workflow clock must be timezone-aware")
        now = now.astimezone(UTC)
        tenant_id = principal.tenant_id
        business_id = None
        attempt = 0
        started = False
        try:
            with self._uow_factory() as uow:
                conversation = uow.conversations.get_for_update(
                    tenant_id=tenant_id, conversation_id=conversation_id,
                )
                if conversation is None:
                    raise AuthorizationError("Workflow access denied")
                business_id = conversation.business_id
                prior = uow.workflow_executions.get_by_key(
                    tenant_id=tenant_id, idempotency_key=idempotency_key, lock=True,
                )
                if prior is not None:
                    if (prior.conversation_id != conversation_id or prior.workflow_name != workflow_name
                            or prior.input_hash != input_hash):
                        raise WorkflowConflictError("idempotency key was used for different workflow input")
                    if prior.status == "succeeded":
                        return WorkflowResult(workflow_name, idempotency_key, prior.status,
                                              prior.attempts, prior.result, replayed=True)
                    if prior.status == "running":
                        raise WorkflowConflictError("workflow execution is already in progress")
                record = uow.workflow_executions.start(
                    tenant_id=tenant_id, business_id=business_id,
                    conversation_id=conversation_id, workflow_name=workflow_name,
                    idempotency_key=idempotency_key, input_hash=input_hash,
                )
                started = True
                attempt = record.attempts
                if attempt > 1:
                    metrics.event("workflow", "retry", "attempted")
                uow.workflows.append(tenant_id=tenant_id, conversation_id=conversation_id,
                                     state="workflow_running", context={"workflow": workflow_name, "attempt": attempt})
                self._event(uow, principal, conversation, "workflow.execution.started",
                            workflow=workflow_name, attempt=attempt)
                output = handler(WorkflowContext(principal, uow, conversation, data, now,
                                                 idempotency_key))
                if not isinstance(output, dict):
                    raise TypeError("workflow handler must return an object")
                # Validate that returned data can be committed to JSON columns.
                json.dumps(output, allow_nan=False)
                final_state = output.get("workflow_state", "workflow_succeeded")
                if not isinstance(final_state, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", final_state):
                    raise ValueError("workflow_state returned by handler is invalid")
                uow.workflows.append(tenant_id=tenant_id, conversation_id=conversation_id,
                                     state=final_state, context={"workflow": workflow_name, "attempt": attempt})
                finished = uow.workflow_executions.finish(
                    tenant_id=tenant_id, idempotency_key=idempotency_key, result=output,
                )
                if finished is None:
                    raise WorkflowConflictError("workflow execution record disappeared")
                self._event(uow, principal, conversation, "workflow.execution.succeeded",
                            workflow=workflow_name, attempt=attempt)
                return WorkflowResult(workflow_name, idempotency_key, "succeeded", attempt, output)
        except Exception as exc:
            metrics.event("workflow", "execution", "failed")
            if started and business_id is not None:
                try:
                    with self._uow_factory() as failure_uow:
                        failure_uow.workflow_executions.fail(
                            tenant_id=tenant_id, business_id=business_id,
                            conversation_id=conversation_id, workflow_name=workflow_name,
                            idempotency_key=idempotency_key, input_hash=input_hash,
                            error_code=type(exc).__name__[:120],
                        )
                        conversation = failure_uow.conversations.get(
                            tenant_id=tenant_id, conversation_id=conversation_id,
                        )
                        if conversation is not None:
                            failure_uow.workflows.append(
                                tenant_id=tenant_id, conversation_id=conversation_id,
                                state="workflow_failed",
                                context={"workflow": workflow_name, "attempt": max(attempt, 1),
                                         "error_code": type(exc).__name__[:120]},
                            )
                            self._event(failure_uow, principal, conversation,
                                "workflow.execution.failed", workflow=workflow_name,
                                attempt=max(attempt, 1), error_code=type(exc).__name__[:120])
                except Exception:
                    # Keep the original error; failure tracking must never hide it.
                    pass
            raise

    @staticmethod
    def _event(uow, principal, conversation, event_type: str, **details) -> None:
        uow.events.add(tenant_id=principal.tenant_id, business_id=conversation.business_id,
            conversation_id=conversation.id, event_type=event_type,
            payload={"actor_id": principal.principal_id, **details},
            source="receptionist_workflow", correlation_id=str(conversation.id))


class ReceptionistActions:
    """Register principal-authorized conversation actions as durable workflows."""

    def __init__(self, engine: ReceptionistWorkflowEngine) -> None:
        self.engine = engine
        for name, handler in (
            ("conversation.assign", self._assign),
            ("conversation.unassign", self._unassign),
            ("conversation.escalate", self._escalate),
            ("conversation.reopen", self._reopen),
            ("conversation.close", self._close),
            ("conversation.schedule_follow_up", self._follow_up),
        ):
            self.engine.register(name, handler)

    def execute(self, principal: AuthenticatedPrincipal, conversation_id: UUID, *,
                action: str, idempotency_key: str,
                input_data: dict[str, Any] | None = None) -> WorkflowResult:
        return self.engine.execute(principal, conversation_id,
            workflow_name=f"conversation.{action}", idempotency_key=idempotency_key,
            input_data=input_data)

    @staticmethod
    def _assign(context: WorkflowContext) -> dict[str, Any]:
        if context.conversation.status == "closed":
            raise WorkflowConflictError("reopen conversation before assignment")
        staff_id = UUID(str(context.input_data["staff_id"]))
        eligible = context.uow.staff.list_active(tenant_id=context.principal.tenant_id,
                                                  business_id=context.conversation.business_id)
        if not any(staff.id == staff_id for staff in eligible):
            raise AuthorizationError("Conversation access denied")
        current = context.uow.conversation_assignments.get(
            tenant_id=context.principal.tenant_id, conversation_id=context.conversation.id)
        if current is not None and current.staff_id == staff_id:
            return {"assigned_staff_id": str(staff_id), "version": context.conversation.version,
                    "workflow_state": "conversation_assigned"}
        updated = context.uow.conversations.change(
            tenant_id=context.principal.tenant_id, conversation_id=context.conversation.id,
            expected_version=context.conversation.version, status=context.conversation.status)
        if updated is None:
            raise WorkflowConflictError("conversation changed during assignment")
        context.uow.conversation_assignments.set(
            tenant_id=context.principal.tenant_id, business_id=context.conversation.business_id,
            conversation_id=context.conversation.id, staff_id=staff_id,
            assigned_by=context.principal.principal_id)
        return {"assigned_staff_id": str(staff_id), "version": updated.version,
                "workflow_state": "conversation_assigned"}

    @staticmethod
    def _unassign(context: WorkflowContext) -> dict[str, Any]:
        if context.conversation.status == "closed":
            raise WorkflowConflictError("reopen conversation before assignment")
        current = context.uow.conversation_assignments.get(
            tenant_id=context.principal.tenant_id, conversation_id=context.conversation.id)
        if current is None or current.staff_id is None:
            return {"assigned_staff_id": None, "version": context.conversation.version,
                    "workflow_state": "conversation_unassigned"}
        updated = context.uow.conversations.change(
            tenant_id=context.principal.tenant_id, conversation_id=context.conversation.id,
            expected_version=context.conversation.version, status=context.conversation.status)
        if updated is None:
            raise WorkflowConflictError("conversation changed during assignment")
        context.uow.conversation_assignments.set(
            tenant_id=context.principal.tenant_id, business_id=context.conversation.business_id,
            conversation_id=context.conversation.id, staff_id=None,
            assigned_by=context.principal.principal_id)
        return {"assigned_staff_id": None, "version": updated.version,
                "workflow_state": "conversation_unassigned"}

    @staticmethod
    def _escalate(context: WorkflowContext) -> dict[str, Any]:
        reason = context.input_data.get("reason")
        priority = context.input_data.get("priority", "normal")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 240:
            raise ValueError("escalation reason is required")
        if priority not in {"normal", "high", "urgent"}:
            raise ValueError("invalid escalation priority")
        ticket = context.uow.escalations.create(
            tenant_id=context.principal.tenant_id, business_id=context.conversation.business_id,
            contact_id=context.conversation.contact_id, conversation_id=context.conversation.id,
            reason=reason.strip(), priority=priority)
        ReceptionistWorkflowEngine._event(context.uow, context.principal, context.conversation,
            "human.escalation.alert", ticket_id=str(ticket.id), priority=priority)
        return {"ticket_id": str(ticket.id), "priority": priority, "workflow_state": "escalated"}

    @staticmethod
    def _set_status(context: WorkflowContext, status: str) -> dict[str, Any]:
        row = context.conversation
        if status == row.status:
            return {"status": status, "version": row.version,
                    "workflow_state": f"conversation_{status}"}
        allowed = {"open": {"closed"}, "pending": {"closed", "open"}, "closed": {"open"}}
        if status not in allowed.get(row.status, set()):
            raise WorkflowConflictError("invalid conversation lifecycle transition")
        updated = context.uow.conversations.change(tenant_id=context.principal.tenant_id,
            conversation_id=row.id, expected_version=row.version, status=status)
        if updated is None:
            raise WorkflowConflictError("conversation changed during workflow execution")
        return {"status": updated.status, "version": updated.version,
                "workflow_state": f"conversation_{status}"}

    @classmethod
    def _reopen(cls, context: WorkflowContext) -> dict[str, Any]:
        return cls._set_status(context, "open")

    @classmethod
    def _close(cls, context: WorkflowContext) -> dict[str, Any]:
        return cls._set_status(context, "closed")

    @staticmethod
    def _follow_up(context: WorkflowContext) -> dict[str, Any]:
        raw = context.input_data.get("scheduled_at")
        if isinstance(raw, str):
            scheduled_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        elif isinstance(raw, datetime):
            scheduled_at = raw
        else:
            raise ValueError("scheduled_at must be an ISO datetime")
        if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
            raise ValueError("scheduled_at must be timezone-aware")
        scheduled_at = scheduled_at.astimezone(UTC)
        if scheduled_at <= context.now:
            raise ValueError("scheduled_at must be in the future")
        reason = context.input_data.get("reason")
        if reason not in {"callback_requested", "missed_response", "nurture"}:
            raise ValueError("invalid follow-up reason")
        state = {"callback_requested": "callback_scheduled",
                 "missed_response": "missed_response_follow_up_scheduled",
                 "nurture": "nurture_scheduled"}[reason]
        task = context.uow.follow_ups.create(tenant_id=context.principal.tenant_id,
            business_id=context.conversation.business_id, conversation_id=context.conversation.id,
            scheduled_at=scheduled_at, reason=reason, workflow_state=state,
            idempotency_key=context.idempotency_key)
        return {"follow_up_id": str(task.id), "scheduled_at": task.scheduled_at.isoformat(),
                "reason": task.reason, "workflow_state": task.workflow_state}
