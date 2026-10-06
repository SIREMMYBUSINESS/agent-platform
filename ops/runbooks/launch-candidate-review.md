# Launch candidate review

**Review date:** 2026-10-05
**Decision:** NO-GO
**Scope:** PostgreSQL, Docker staging runtime, Prometheus/Grafana, provider integrations, and production smoke tests.

## Evidence

- Full suite with PostgreSQL enabled: 290 passed, 1 skipped, 5 warnings. The remaining skip is a POSIX file-permission check that does not apply on Windows.
- Current provider/readiness test run: 18 passed, 1 skipped. These tests use test doubles and do not contact provider accounts.
- PostgreSQL 18.6 was extracted to the temporary directory and started loopback-only on port `55432`. The live integration test passed migration upgrade, downgrade and re-upgrade through `0013_provider_delivery_receipts`, inbound persistence, tenant-scoped schema checks, and rollback verification.
- Live PostgreSQL validation uncovered and fixed JSON colon bind parsing in migration `0007_geo_policy_engine`; the integration fixture now provisions explicit business hours and verifies rollback using the current intake service interface.
- Compose configuration parses when its three required secret-path variables are supplied. Docker Desktop is installed, but its service remains stopped and its backend has been waiting for the engine health endpoint for more than ten minutes (HTTP 503); `docker info` cannot connect to the engine pipe. The host exposes the older inbox `wsl.exe`; `wsl --status` returns access denied. Enabling the Windows Subsystem for Linux and Virtual Machine Platform requires administrator elevation; DISM returned error 740 in this session. No image build, container runtime, or staging deployment was verified.
- Prometheus YAML and Grafana dashboard JSON parse. Neither service is installed or running, so scraping, dashboard queries, and alert delivery were not verified.
- The secret-file integration is present in the production Compose example and runbook, but no database/Redis secret-file paths or provider secret directory are configured in this environment. No credentials are present for Twilio, SMTP, HubSpot, or Salesforce. No live sends, receipts, CRM writes, or CRM retries were performed.
- `/health` returned HTTP 200 from the locally running Uvicorn server. An in-process `/ready` request configured with the temporary PostgreSQL URL returned HTTP 200 while that database was running; the current server `/ready` request times out because the temporary PostgreSQL server is no longer running.
- PostgreSQL binaries and the test cluster are temporary, not registered as a machine-wide Windows service. The test server is bound to loopback and uses local trust authentication.

## Blocking release gates

1. For persistent certification, provide a managed/dedicated PostgreSQL environment and verify backup restore; local migration/integration validation passed.
2. Have a Windows administrator enable/update WSL 2 and Virtual Machine Platform (and reboot if requested); then start Docker Desktop, build and run the staging image, migrations, and health checks.
3. Run Prometheus and Grafana in staging; verify authenticated scraping, dashboards, and alerts.
4. Provision database/Redis and provider credentials through protected secret mounts, plus approved test records/destinations; validate Twilio, SMTP, HubSpot, and Salesforce end to end.
5. Run production smoke tests through staging ingress, including authenticated metrics, delivery receipts, CRM idempotency, tenant isolation, and rollback.

No customer traffic should be enabled until all gates have recorded evidence and a new candidate review approves the release.
