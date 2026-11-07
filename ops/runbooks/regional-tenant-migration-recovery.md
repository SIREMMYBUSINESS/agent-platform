# Regional tenant migration and recovery

This procedure is for the planned tenant-home-region data plane described in [the architecture decision](../architecture/tenant-regional-data-plane.md). The runtime routing and tenant migration tooling are not implemented yet. Until they are, do not use this procedure to move a live tenant or claim regional residency.

## Before the migration window

1. Record tenant ID, source region, destination region, approved change ticket, operator, and maintenance window. Do not put credentials or customer content in the ticket.
2. Confirm the directory assignment is `active` in the source region and that the destination region is enabled and approved for the tenant. Reject a move that widens storage permissions without separate approval.
3. Provision the destination PostgreSQL service in the destination region. Provision its secret through the approved secret manager; require certificate-verified TLS. Do not copy a source-region database URL into destination service logs or tickets.
4. Run the current Alembic head on the empty destination using the one-off migration job. Confirm schema version and extensions before importing data.
5. Take a provider-managed backup or a verified encrypted logical backup of the source database. Restore that backup into an isolated database and validate it before scheduling the tenant move. Record backup ID, timestamp, and restore result.
6. Check destination capacity, connection limits, encryption, network policy, monitoring, alerting, and backup/PITR coverage. Confirm operators have a tested recovery path that does not depend on the source region.

## Cutover

1. Set the tenant assignment to `migrating` in the directory. Routing must reject new writes for this tenant; it must not fall back to another region.
2. Pause tenant-scoped API writes and provider webhook ingestion. Stop or drain regional workers for the tenant. Allow already-running tasks to finish; park retryable pending work. Do not move a task while its handler is running.
3. Record counts for every tenant-owned table and queue status counts. Verify there are no running/leased tasks. Record the latest committed tenant audit/outbox position.
4. Export the tenant's rows with a versioned, reviewed migration tool that follows the declared foreign-key dependency order. Do not use ad hoc table copies or include other tenants. Encrypt the export in transit and at rest; restrict access and delete temporary copies after the retention window.
5. Import into the destination in one controlled migration operation. Preserve primary IDs, idempotency keys, audit timestamps, and provider delivery identifiers. Keep task state and outbox dedupe keys intact so a retry cannot duplicate a side effect.
6. Validate source/destination row counts per table, foreign-key/check constraints, deterministic checksums over canonicalized tenant rows, tenant scoping, and queue state. Confirm no other tenant rows were copied. Verify the destination has the required approved regional config.
7. Run a provider-free canary read and queue task in the destination. Confirm the destination worker completes only its regional task and its heartbeat reports the destination region.
8. Atomically change the directory assignment to the destination and `active`. Resume writes and workers. Observe readiness, routing denials, task retries, duplicate protection, and error rates through the agreed observation window.
9. Keep the source tenant snapshot read-only and retained through the rollback window. Do not accept writes in both regions.
10. After migration approval and retention expiry, remove the source tenant copy using the approved deletion workflow and record the completion audit.

## Abort and rollback

- Before the directory cutover, keep the source authoritative. Discard the incomplete destination tenant import after preserving diagnostic metadata that contains no customer content.
- If validation fails after import but before cutover, set the directory back to the source only after confirming source remains unchanged; keep routing in `migrating` until that assertion is proven.
- If destination traffic has started, stop tenant writes and workers first. Determine whether destination has committed writes. Do not simply point back to the old source if writes would be lost.
- Reconcile committed destination writes using the reviewed reverse-migration procedure, or restore the destination and keep the tenant paused. Require operator approval before changing the directory assignment.
- Once source is again authoritative and validated, update the directory assignment and state atomically, resume only source-region workers, and verify the canary and health checks.
- Never use a stale source backup as a rollback target after destination writes without an explicit data-loss decision and recorded approval.

## Regional database recovery

1. Declare the affected region and database unavailable; pause tenant writes and stop workers that cannot reach their assigned database. Do not redirect tenants to another region unless its storage use is approved and the recovery procedure is explicitly authorized.
2. Restore the latest eligible backup/PITR point into a new isolated database in the tenant's home region. Keep the failed database untouched for investigation where possible.
3. Apply only reviewed forward migrations to the restored database. Verify TLS, schema head, constraints, table counts, tenant checksums, and queue leases/retries.
4. If recovery point is older than the latest committed outbox/queue position, reconcile from approved durable logs before resuming workers. Preserve idempotency keys and provider delivery receipts.
5. Run provider-free read/write and queue canaries. Confirm API and worker region IDs match the database region and health checks report the restored database and worker healthy.
6. Re-enable tenant routing only after data-loss assessment and recovery approval. Monitor queue age, retry/dead-letter counts, and region-specific health through the recovery window.
7. Record actual recovery point, elapsed recovery time, data reconciliation, and remaining customer impact. Compare them with the approved RPO/RTO; do not claim the target was met without measurements.

## Required tooling not yet present

The cutover is intentionally blocked until the project has a tenant-scoped export/import tool, assignment state machine, source/destination verification report, safe pause/drain controls, and tested rollback automation. Do not improvise these operations with manual SQL against customer data.
