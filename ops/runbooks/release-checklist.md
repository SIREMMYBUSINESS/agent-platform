# Release certification checklist

Complete this checklist in the target staging environment before production traffic is enabled. Record the image digest, migration head, provider test IDs, and approver in the release ticket. Never record credentials or message content.

## PostgreSQL validation

- [ ] Use an isolated disposable PostgreSQL database ending in `_test`; set `POSTGRES_TEST_DATABASE_URL` and run `python -m pytest -v -m postgres tests/test_postgres_integration.py`.
- [ ] Confirm `alembic upgrade head` completes once as a deployment job and `alembic current` reports the repository head (`0013_provider_delivery_receipts`).
- [ ] Confirm `/ready` succeeds against the target database and fails closed with a generic 503 when PostgreSQL is unavailable.
- [ ] Verify a managed backup and a restore into an isolated database before launch.

## Docker validation

- [ ] Run `docker compose -f ops/compose.production.yml.example config` with the real deployment variable names set.
- [ ] Build and start the release image in staging; record its immutable digest.
- [ ] Confirm the app runs as UID/GID `10001`, has a read-only root filesystem, has no added capabilities, and passes its `/ready` health check.
- [ ] Confirm migrations run as a one-off job and application replicas do not independently migrate.

## Provider validation

- [ ] With staging credentials and approved test destinations, verify each enabled email, SMS, and WhatsApp provider accepts a test send; check the provider ID is stored without logging credentials or message content.
- [ ] Replay the same delivery callback and confirm idempotency; deliver a prior-attempt callback after retry and confirm it cannot change the current attempt.
- [ ] Verify valid signatures are accepted and invalid signatures, altered callback parameters, and wrong tenant/business scope are rejected.
- [ ] Verify CRM sync retry with the same idempotency key does not create duplicate contacts or activities; record provider request IDs only.

## Production smoke tests

- [ ] Through the production ingress, verify `/health` returns success and `/ready` returns success after migrations.
- [ ] Verify authenticated `/metrics` access and confirm metric labels remain bounded and contain no tenant, contact, campaign, or provider IDs.
- [ ] Run one approved inbound receptionist scenario, one test outreach send with a delivery receipt, and one CRM sync; verify tenant scope, audit events, and persisted results.
- [ ] Confirm dashboards and failure/retry alerts receive staging signals; verify rollback to the previous image is documented and executable.

## Release decision

- [ ] All checkboxes above are evidenced in the release record; unresolved checks remain release blockers.
- [ ] Confirm on-call ownership, escalation contacts, rollback owner, and the maintenance window before enabling customer traffic.
