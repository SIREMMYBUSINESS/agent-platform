# Production observability

The API and workers emit JSON logs to stdout. Every formatted record has a
`correlation_id`; records inside a request/task also include `trace_id` and
`span_id`. Trusted tenant and business scope is included on business-critical
service and worker logs. Do not add message text, contact details, provider
credentials, webhook payloads, or caller-supplied tenant metadata to logs.

## Metrics and health

Prometheus scrapes the authenticated `/metrics` endpoint using a dedicated
`metrics_reader` bearer credential. Queue depth is sampled from the durable
database ledger. Business and campaign dimensions use stable hashed buckets;
raw tenant, business, campaign, contact, and lead IDs are never metric labels.
The protected endpoint refreshes queue-depth gauges at scrape time.

`/health` is a liveness response. `/ready` checks the database. The internal
`/health/components` response checks database, Redis, and Celery worker queues;
provider APIs are reported as `not_probed` because active checks can incur cost
or mutate provider state. It returns only component status, never connection
strings or exception details.

Import `ops/prometheus/alerts.yml.example` through Prometheus `rule_files` and
import `ops/grafana/agent-platform-overview.json` into Grafana. Alert thresholds
are starting points; tune them against each deployment's traffic and SLOs.

## Traces

OpenTelemetry uses W3C Trace Context and a parent-based sampler (10% default).
Set `OTEL_EXPORTER_OTLP_ENDPOINT` to an OTLP/HTTP collector base URL to export
traces and metrics; without it, spans and instruments remain local. Deploy an
OpenTelemetry Collector between app/worker processes and the tracing backend.
Keep credentials and tenant IDs out of baggage. Durable queue tasks carry the
parent `traceparent`; the worker creates a child span for execution.

## Centralized logs and retention

Use `ops/logging/fluent-bit.conf.example` as a Fluent Bit starting point for
Docker JSON logs and OpenSearch. Mount the Docker log directory read-only and
provide OpenSearch credentials through a secret manager. Configure an
OpenSearch Index State Management policy for the `agent-platform-*` index
pattern; set hot retention to 30 days and deletion at 90 days, or use the
organization's approved retention schedule. Never put credentials in this
configuration file. Restrict index access by environment and support queries
on `tenant_id`, `business_id`, `correlation_id`, `trace_id`, and `level`.

## Dashboard panels

The dashboard covers the intake funnel, campaign delivery/replies, appointment
bookings and conversions, queue depth and wait/execution latency, task/provider
errors, policy blocks, and worker heartbeat. It aggregates business scope into
bounded buckets to avoid high-cardinality series. Use database-backed tenant
reporting APIs for exact customer-level analytics.
