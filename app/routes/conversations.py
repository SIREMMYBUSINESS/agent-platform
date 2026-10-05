"""Conversation management transport; authorization belongs to the service."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException

from app.auth.dependencies import get_authenticated_principal
from app.conversation_schemas import (
    ConversationAssignmentRequest, ConversationCreateRequest, ConversationListResponse,
    ConversationReadResponse, ConversationStatusRequest,
)
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.conversation_service import ConversationService
from app.workflow_schemas import ConversationWorkflowRequest, ConversationWorkflowResponse
from app.workflows.receptionist import (
    ReceptionistActions, ReceptionistWorkflowEngine, WorkflowConflictError,
    WorkflowNotFoundError,
)


router = APIRouter(prefix="/conversations", tags=["conversations"])


def get_conversation_service() -> ConversationService:
    return ConversationService(uow_factory=lambda: UnitOfWork(session_factory))


def get_receptionist_actions() -> ReceptionistActions:
    engine = ReceptionistWorkflowEngine(uow_factory=lambda: UnitOfWork(session_factory))
    return ReceptionistActions(engine)


@router.post("", response_model=ConversationReadResponse, status_code=201)
def create_conversation(
    request: ConversationCreateRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    service: ConversationService = Depends(get_conversation_service),
):
    return service.create(principal, contact_id=request.contact_id, channel=request.channel)


@router.get("", response_model=ConversationListResponse)
def list_conversations(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    service: ConversationService = Depends(get_conversation_service),
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
):
    return ConversationListResponse(items=service.list(principal, offset=offset, limit=limit), offset=offset, limit=limit)


@router.get("/{conversation_id}", response_model=ConversationReadResponse)
def get_conversation(
    conversation_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    service: ConversationService = Depends(get_conversation_service),
):
    return service.get(principal, conversation_id)


@router.patch("/{conversation_id}/status", response_model=ConversationReadResponse)
def change_status(
    conversation_id: UUID, request: ConversationStatusRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    service: ConversationService = Depends(get_conversation_service),
):
    return service.transition(principal, conversation_id, status=request.status, expected_version=request.expected_version)


@router.put("/{conversation_id}/assignment", response_model=ConversationReadResponse)
def assign_conversation(
    conversation_id: UUID, request: ConversationAssignmentRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    service: ConversationService = Depends(get_conversation_service),
):
    return service.assign(principal, conversation_id, staff_id=request.staff_id, expected_version=request.expected_version)


@router.post("/{conversation_id}/workflows/{action}", response_model=ConversationWorkflowResponse)
def execute_conversation_action(
    conversation_id: UUID, action: str, request: ConversationWorkflowRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    actions: ReceptionistActions = Depends(get_receptionist_actions),
):
    if action not in {"assign", "unassign", "escalate", "reopen", "close", "schedule_follow_up"}:
        raise HTTPException(404, "Workflow action not found")
    if action == "assign" and request.staff_id is None:
        raise HTTPException(422, "staff_id is required for assignment")
    if action == "escalate" and request.escalation_reason is None:
        raise HTTPException(422, "escalation_reason is required for escalation")
    if action == "schedule_follow_up" and (request.scheduled_at is None or request.follow_up_reason is None):
        raise HTTPException(422, "scheduled_at and follow_up_reason are required for follow-up")
    input_data = {}
    if action == "assign":
        input_data = {"staff_id": str(request.staff_id)}
    elif action == "escalate":
        input_data = {"reason": request.escalation_reason, "priority": request.priority}
    elif action == "schedule_follow_up":
        if request.scheduled_at.tzinfo is None or request.scheduled_at.utcoffset() is None:
            raise HTTPException(422, "scheduled_at must include a timezone")
        input_data = {"scheduled_at": request.scheduled_at.isoformat(), "reason": request.follow_up_reason}
    try:
        result = actions.execute(principal, conversation_id, action=action,
            idempotency_key=request.idempotency_key, input_data=input_data)
    except WorkflowConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(404, "Workflow action not found") from exc
    return ConversationWorkflowResponse(**result.__dict__)
