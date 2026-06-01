"""Application-level metrics for Prometheus scraping.

Public surface:
    - `init_registry()` — call once at app startup to honour
      PROMETHEUS_MULTIPROC_DIR (multi-worker mode) or use the default
      in-process registry. Wipes stale mmap files on multiproc init.
    - `metrics` — the module of metric definitions. Hooks throughout
      the codebase do `from sheaf.observability.metrics import (
      auth_logins_total, ...)` and bump them.
    - `MetricsMiddleware` — the HTTP RED middleware mounted in main.py.
    - `setup_metrics_endpoint(app, settings)` — wires the /metrics
      endpoint per settings (separate listener or main-app mount).
    - `refresh_gauges()` — the background updater registered as a job.

Cardinality rule: no `*_id`, email, or per-user labels, ever. Per-IP /
per-account volume is captured via histograms of rates, not labels.
"""

from sheaf.observability.endpoint import setup_metrics_endpoint
from sheaf.observability.middleware import MetricsMiddleware
from sheaf.observability.registry import get_registry, init_registry

__all__ = [
    "MetricsMiddleware",
    "get_registry",
    "init_registry",
    "setup_metrics_endpoint",
]
