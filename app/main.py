from fastapi import FastAPI

from app.schemas import InboundRequest, InboundResponse
from app.tasks import TaskDispatcher

app = FastAPI(title="Receptionist + Outreach Agent Platform")
dispatcher = TaskDispatcher()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/inbound", response_model=InboundResponse)
def inbound(request: InboundRequest):
    result = dispatcher.handle_inbound(request.model_dump())
    return InboundResponse(
        success=result["success"],
        status=result["status"],
        workflow_state=result["workflow_state"],
        agent_response=result.get("agent_response"),
        reason=result.get("reason"),
    )
