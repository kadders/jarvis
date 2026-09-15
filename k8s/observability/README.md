# Observability samples (document-first)

Uncomment and adapt when you adopt Prometheus Operator / Grafana.

- `servicemonitor-llm-api.yaml.example` — scrape the LLM API on **`GET /metrics`** (port **`http`**, path **`/metrics`**) when metrics are exposed from **`jarvis-llm-api`**.

These files are **not** applied by the default `k8s/kustomization.yaml` overlay.

See [Observability](../../docs/operations/observability.md) for VictoriaMetrics, Prometheus + Grafana, OpenTelemetry Collector, and log fields (**`LOG_JSON`**, **`X-Request-ID`**).
