# Tenant regional data plane

## Status

Accepted architecture direction. The first code slice adds an assignment table, migration backfill, and an opt-in authentication gate that serves only tenants assigned to the process's local database region. Separate regional database provisioning, forwarding, regional worker isolation, tenant transfer tooling, and production rollout remain pending.

The current Railway deployment stores application data in the EU West (Amsterdam) PostgreSQL service. `TENANT.default_region_id`, `BUSINESS.region_id`, and `QUEUE_TASK_RECORD.region_id` are policy and workflow metadata; they do not select a physical database. The application currently creates one `SessionFactory` from one `DATABASE_URL`, and the worker polls that database's queue. Do not treat region settings or a healthy regional-access check as proof that data is physically stored in that region.

## Decision

Use a minimal global control database and one customer-data database per supported home region.

The global directory may contain only:

- immutable tenant ID;
- tenant home-region ID and routing status;
- API-key verification material and tenant-scoped grants needed to authenticate and route requests;
- routing/schema metadata and the minimum operational audit needed to administer assignments.

Do not put customer names, contacts, conversations, messages, appointments, campaign data, provider payloads, or customer event history in the global directory. Review whether even identifiers and authentication metadata may be stored centrally for each launch jurisdiction before production approval.

Each regional database owns the tenant's customer data, tenant region configuration, businesses, consent and policy records, operational audit, and durable task queue. A tenant's customer data, queue, and worker execution stay in the assigned home region. Regional API/worker services receive credentials only for their own regional database. A request for a tenant assigned elsewhere must be forwarded to that region through an authenticated service boundary or rejected; it must never fall back to the local default database.

Tenant assignment is derived from the trusted directory after verifying the credential. A caller-supplied tenant ID, region ID, business ID, hostname, or queue payload is never routing authority. Assignment changes require an explicit audited migration state machine; ordinary region preference changes must not move stored data.

## Required runtime behavior

- Authentication resolves the API key and grant against the control database, then resolves the immutable home-region assignment.
- Authentication resolves the API key and grant, then reads the assignment from the control directory. The current process has one configured regional database; it opens tenant-data sessions only after the assignment matches that database's configured region. A request sent to the wrong regional API receives `421` with `X-Tenant-Home-Region`; the edge gateway must route or reject it.
- Missing assignments and inactive tenants fail closed. Assignments in `migrating` state return `503` before tenant-data access.
- A regional API/worker process has one configured database region. Startup verifies that deployment region, database region, and service configuration agree.
- A worker polls only its own regional database and claims only tasks assigned to that region. Queue creation happens in the same transaction and database as the domain write that caused the work.
- Provider calls remain behind adapters and their data-transfer authorization. This architecture does not validate provider credentials, destinations, or provider residency.
- Readiness reports each required local dependency. It must not report a remote region healthy just because the local database responds.
- Region moves are explicit data migrations, not live dual writes. During a move, the tenant is paused, in-flight work is drained or parked, and the source remains recoverable until destination verification is complete.

## Implementation sequence

1. **In progress:** add the control-directory assignment schema, backfill, and authenticated request guard. Keep customer data out of the directory.
2. Add directory provisioning and assignment-audit services, then separate API-key/grant storage into the control database without retaining customer tenant details there.
3. Add an authenticated regional API routing boundary and session-factory registry. Require explicit region selection; keep the single-database path only as a development compatibility mode. The current guard rejects authenticated requests sent to the wrong region with 421; gateway forwarding is still pending.
4. Preserve tenant scoping inside every regional repository/service and prove that a caller cannot override the resolved assignment.
5. Make regional service configuration explicit: one database secret per region, matching `DATABASE_REGION_ID` and `DEPLOYMENT_REGION_ID`, separate API and worker deployments, and no cross-region database credentials in the process.
6. Make workers region-pinned. The current worker requires matching deployment/database region settings when routing is enabled and filters queue selection and claims by its database region. Add region-aware worker identity/heartbeat keys and health checks. Add an outbox handoff only for work that intentionally crosses a region boundary; such transfers require policy authorization and an audit record.
7. Add the tenant migration state machine and operator commands/runbook. Block assignment changes unless source snapshot, destination schema, checksums, queue state, and rollback point have been verified.
8. Deploy empty regional databases first. Migrate a synthetic tenant, verify fail-closed routing and worker isolation, and rehearse restore and rollback before moving any customer tenant.
9. Keep live provider validation as an independent release gate, using approved provider sandbox credentials and test destinations.

## Release gates

Do not onboard a tenant outside the EU database until all are true:

- directory routing and regional session isolation are implemented and reviewed;
- no application or worker process has credentials for an unauthorized region;
- tenant identity cannot be overridden by request or task data;
- per-region migrations are repeatable and report the same Alembic head;
- regional queue tasks cannot be claimed by another region;
- tenant migration and restore have been rehearsed from backup into an isolated target;
- data counts, key constraints, and tenant-scope invariants pass destination validation;
- RPO, RTO, backup retention, and regional outage behavior are approved and monitored;
- provider validation is separately complete for every enabled integration.

## Current limitation

The assignment table and opt-in guard are not enabled in Railway and do not split customer data from the current Amsterdam database. The existing API-key and grant tables still depend on the legacy tenant table, so their metadata must be separated before the control database can be minimal. Public webhook/capability routes currently fail closed with 503 while routing is enabled; directory-based resolution and authenticated forwarding remain pending. No production customer-region guarantee should be made until the control database schema, regional databases, all request paths, regional workers, forwarding, migration tooling, and release gates above are implemented and verified.
