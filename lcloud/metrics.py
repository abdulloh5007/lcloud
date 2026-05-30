"""Prometheus monitoring scaffolding.

Exposes:
  GET /metrics  — Prometheus text format

Out of the box gets:
  - http_requests_total (Counter, labelled by method/handler/status)
  - http_request_duration_seconds (Histogram)
  - http_request_size_bytes (Histogram)
  - http_response_size_bytes (Histogram)

Custom counters added via prometheus_client (used in app code):
  - lcloud_uploads_total (by mime, compressed flag)
  - lcloud_uploaded_bytes_total
  - lcloud_payment_requests_total (by status)
  - lcloud_pin_recovery_attempts_total (by outcome)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)


# ----------------------------------------------------------- custom counters


uploads_counter = Counter(
    "lcloud_uploads_total",
    "Total file uploads",
    labelnames=["mime_class", "compressed", "caption_kind"],
)

uploaded_bytes_counter = Counter(
    "lcloud_uploaded_bytes_total",
    "Total bytes uploaded (post-compression)",
)

payment_requests_counter = Counter(
    "lcloud_payment_requests_total",
    "Payment requests submitted",
    labelnames=["outcome"],  # submitted | duplicate | rate_limited
)

payment_decisions_counter = Counter(
    "lcloud_payment_decisions_total",
    "Admin decisions on payment requests",
    labelnames=["decision"],  # approved | rejected
)

pin_attempts_counter = Counter(
    "lcloud_pin_recovery_attempts_total",
    "PIN recovery attempts",
    labelnames=["outcome"],  # ok | wrong | locked | rate_limited | not_found
)

active_users_gauge = Gauge(
    "lcloud_active_users",
    "Number of users not suspended",
)

total_storage_gauge = Gauge(
    "lcloud_storage_used_bytes",
    "Sum of all users' storage_used_bytes",
)

share_downloads_counter = Counter(
    "lcloud_share_downloads_total",
    "Anonymous downloads via /share/{token}",
)

cache_ops_counter = Counter(
    "lcloud_cache_ops_total",
    "TTL cache operations",
    labelnames=["op"],  # hit | miss
)

upload_sign_seconds = Histogram(
    "lcloud_upload_sign_seconds",
    "Time spent signing uploads",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)


# ----------------------------------------------------------- wire-up


def install_metrics(app: FastAPI) -> None:
    """Mount Prometheus instrumentation + /metrics endpoint."""
    try:
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError:
        logger.warning("prometheus-fastapi-instrumentator not installed; /metrics disabled")
        return

    instr = Instrumentator(
        excluded_handlers=["/metrics", "/health"],
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=False,
    )
    instr.instrument(app)
    instr.expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        should_gzip=True,
    )
    logger.info("Prometheus metrics installed at /metrics")


__all__ = [
    "active_users_gauge",
    "cache_ops_counter",
    "install_metrics",
    "payment_decisions_counter",
    "payment_requests_counter",
    "pin_attempts_counter",
    "share_downloads_counter",
    "total_storage_gauge",
    "upload_sign_seconds",
    "uploaded_bytes_counter",
    "uploads_counter",
]
