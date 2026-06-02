"""Prometheus registry setup.

When PROMETHEUS_MULTIPROC_DIR is set, metrics are written to mmap
files in that directory and a MultiProcessCollector aggregates them
at scrape time. Stale files from a previous container life are wiped
at startup to avoid resurrected readings.

When the env var is unset (tests, local single-process), the default
in-process REGISTRY is used.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from prometheus_client import REGISTRY, CollectorRegistry
from prometheus_client.multiprocess import MultiProcessCollector

logger = logging.getLogger("sheaf.metrics")

_registry: CollectorRegistry | None = None


def init_registry() -> CollectorRegistry:
    """Initialise and return the metrics registry.

    Safe to call more than once: subsequent calls return the same object.
    """
    global _registry
    if _registry is not None:
        return _registry

    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir:
        path = Path(multiproc_dir)
        path.mkdir(parents=True, exist_ok=True)
        # Wipe stale files from previous container lives so resurrected
        # counter values don't bleed across deploys.
        for f in path.glob("*.db"):
            try:
                f.unlink()
            except OSError as exc:
                logger.warning("could not remove stale metrics file %s: %s", f, exc)
        registry = CollectorRegistry()
        MultiProcessCollector(registry)
        logger.info("metrics registry: multiproc mode at %s", multiproc_dir)
        _registry = registry
    else:
        _registry = REGISTRY
        logger.info("metrics registry: in-process mode")

    return _registry


def get_registry() -> CollectorRegistry:
    """Return the active registry, initialising on first call."""
    if _registry is None:
        return init_registry()
    return _registry
