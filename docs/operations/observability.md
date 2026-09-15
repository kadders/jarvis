# Observability

The LLM API exposes **JSON logs** (optional), **request correlation** via **`X-Request-ID`**, and a Prometheus **`GET /metrics`** scrape endpoint (homelab-friendly; add auth at the edge for production).

## Request IDs

- Clients may send **`X-Request-ID`** or **`X-Correlation-ID`**; if omitted, the server generates a UUID.
- The same value is returned on the response as **`X-Request-ID`** and attached to **loguru** records (`request_id` in JSON mode).
- Outbound calls from the API (**Ollama** completions, OCR **webhooks**) forward **`X-Request-ID`** when present.

## Structured logs

| Variable | Description |
| -------- | ----------- |
| **`LOG_LEVEL`** | Loguru level (default **`INFO`**). |
| **`LOG_JSON`** | If `true`, one JSON object per line (suitable for **Loki**, **Elasticsearch**, **Datadog** agents, or **journald** parsers). |

## Prometheus metrics

| Endpoint | Notes |
| -------- | ----- |
| **`GET /metrics`** | **`prometheus_client`** exposition; unauthenticated in the stock image — restrict at your ingress or network policy. |

Useful series include **`jarvis_http_requests_total`**, **`jarvis_http_request_duration_seconds`**, **`jarvis_events_post_total`**, **`jarvis_idempotency_replay_total`**, **`jarvis_ocr_recognize_total`**, and **`jarvis_ocr_recognize_duration_seconds`**.

## Self-hosted stacks (pick one)

These are common **operator-run** patterns (no cluster commands from CI here):

1. **VictoriaMetrics** (single-binary or cluster): point **`vmagent`** or VM single at **`http://<llm-api>:8000/metrics`**; long-term storage and Grafana datasource in one project.
2. **Prometheus + Grafana**: Prometheus scrape job on pod port **8000**, path **`/metrics`**; Grafana dashboards from Prometheus or Grafana Cloud (self-hosted).
3. **OpenTelemetry Collector**: scrape Prometheus format from the app (`prometheus` receiver) and export via **`prometheusremotewrite`** to VictoriaMetrics or Mimir, or **`otlp`** to a tracing backend.
4. **Grafana LGTM** (Docker bundle) or **Mimir** for larger retention — same scrape target.

For Kubernetes, commented samples live under **`k8s/observability/`** (see **`servicemonitor-llm-api.yaml.example`** for Prometheus Operator).

When adding instrumentation, update this page and [Environment variables](../reference/environment-variables.md) together.
