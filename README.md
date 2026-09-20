# Agent Platform Skeleton

This project is the Phase 0 foundation for a receptionist + outreach AI platform.

It includes:
- tenant and conversation models
- workflow state machine
- policy checker
- event bus
- inbound API
- receptionist agent

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn pydantic
uvicorn app.main:app --reload
