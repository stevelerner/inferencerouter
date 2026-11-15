# Task: kind-aware-red

## Goal
`get_service_catalog` counts every span of a service, so a proxied hop (CLIENT span in the caller, SERVER span in the callee) double counts rate and error rate. Make rate, error_rate and the latency percentiles use only server-side spans.

## Read
docker/apps/tinyolly-common/tinyolly_common/storage_sqlite.py
docker/apps/tinyolly-common/tests/test_catalog_kind.py

## May change
docker/apps/tinyolly-common/tinyolly_common/storage_sqlite.py

## Constraints
- Only `get_service_catalog` changes. Do not edit the tests.
- Counted spans for a service: those with kind SERVER (2 or "SPAN_KIND_SERVER") or CONSUMER (5 or "SPAN_KIND_CONSUMER"). If a service has none, count all of its spans (fallback).
- `error_rate` (percent, 2 decimals), `duration_p50/p95/p99` use counted spans only. Same formulas as today.
- `rate` is requests per second as a float rounded to 2 decimals: counted spans / window, window = (latest end time - earliest start time of the counted spans) in seconds, at least 1.0. It is 0 when there are no spans. This replaces `max(1, round(span_count / 300))`.
- `span_count`, `trace_count`, `first_seen`, `last_seen` still cover every span of the service.
- Keep the output keys and cache behaviour unchanged.

## Done when
cd docker/apps/tinyolly-common && PYTHONPATH=. /Volumes/external/code/tinyolly/.venv-tests/tinyolly-common/bin/python -m pytest tests -q
