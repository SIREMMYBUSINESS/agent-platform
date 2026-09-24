# Project Engineering Guide

This repository is a modular, multi-tenant receptionist and outreach agent platform. Keep changes aligned with the rules below and with the current API behavior.

## Engineering rules

1. Preserve the modular architecture. Keep API, business logic, persistence, provider integrations, and agent behavior in their appropriate modules.
2. Never bypass tenant isolation. Every tenant-owned operation must be scoped to an authenticated and authorized tenant.
3. API schemas must be explicit and validated. Do not rely on unvalidated dictionaries for public request or response contracts.
4. Keep business logic out of FastAPI route handlers. Routes should validate/translate requests, call an application service, and return a response.
5. Database access should go through repository abstractions as persistence is introduced; avoid coupling business logic directly to ORM sessions or models.
6. Put external communication and other provider integrations behind adapters.
7. Put LLM providers behind adapters. Do not couple workflows or agents to a specific provider SDK.
8. Background tasks must be idempotent so retries and duplicate delivery do not repeat side effects.
9. Never put secrets or unnecessary personally identifiable information (PII) in logs.
10. Add tests for new behavior and for behavior changed by a fix.
11. Prefer small, safe changes over large rewrites.
12. Do not silently change existing API contracts. Document and test any intentional contract change.
13. Keep Phase 0 behavior backward-compatible unless a test demonstrates why it must change.

## Existing modules

- `app/main.py` — FastAPI application, `/health` and `/inbound` routes, and dispatcher wiring.
- `app/schemas.py` — Pydantic request and response schemas used by the API.
- `app/models.py` — enums and domain-facing Pydantic models, including inbound and entity creation shapes.
- `app/tasks.py` — synchronous inbound dispatcher coordinating policy, workflow, events, and receptionist response.
- `app/workflows.py` — in-memory workflow state engine.
- `app/events.py` — event envelope and in-memory event bus.
- `app/agents.py` — base agent and deterministic receptionist response implementation.
- `app/policies.py` — region/channel consent policy checks.
- `app/config.py` — application settings, including database and Redis URLs.
- `app/app_db_models_Version2.py` — SQLModel table definitions for tenants, businesses, contacts, conversations, messages, appointments, workflow states, and event logs.
- `app/app_db_Version2.py` — SQLModel engine setup and database initialization/session helpers. The current inbound route does not yet use this persistence path.
- `app/app_celery_app_Version2.py` — Celery app configured with Redis as broker and result backend. The current inbound route does not yet enqueue Celery work.
- `tests/test_inbound.py` — current API smoke tests for health and a successful inbound request.

The `Version2` module names are existing filenames; do not rename them as incidental cleanup or break existing imports without a deliberate, tested change.

## Verification commands

Run the complete test suite from the repository root using the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

The cache provider is disabled to avoid pytest cache writes during verification. To run the API locally with automatic reload:

```powershell
python -m uvicorn app.main:app --reload
```

The local API health check is `GET http://127.0.0.1:8000/health`. Do not claim tests or other verification passed unless the command was run and its result checked.
