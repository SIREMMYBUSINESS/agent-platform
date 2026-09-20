from fastapi import FastAPI
from app.schemas import InboundRequest
from app.tasks import TaskDispatcher

app = FastAPI(title="Receptionist & Outreach Agent Platform")
dispatcher = TaskDispatcher()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/inbound")
def inbound(request: InboundRequest):
    return dispatcher.handle_inbound(request.model_dump())
