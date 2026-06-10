"""Unit tests for the multiprocess metrics registry invariants.

These run a child interpreter with PROMETHEUS_MULTIPROC_DIR set,
because multiprocess mode is decided at import time and must not leak
into the rest of the suite (which runs the in-process registry path).
No server stack needed - pure library behaviour.

Regression coverage for the duplicate-exposition bug: metric objects
registered into the same registry as the MultiProcessCollector made
generate_latest() emit every family twice - the live object's
per-process view (zeros for gauges another worker maintains) plus the
collector's real cross-process aggregate. Scrapers keep whichever
sample they parse first, so dashboards randomly showed zeros.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def _run_child(code: str, extra_env: dict[str, str] | None = None,
               drop: tuple[str, ...] = ()) -> str:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(extra_env or {})
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_multiproc_exposition_has_no_duplicate_families(tmp_path):
    body = _run_child(
        """
        from prometheus_client import generate_latest
        from sheaf.observability.registry import get_registry
        from sheaf.observability import metrics

        metrics.users_total.set(6)
        metrics.http_requests_total.labels(
            method="GET", route="/health", status_class="2xx"
        ).inc()

        print(generate_latest(get_registry()).decode())
        """,
        extra_env={"PROMETHEUS_MULTIPROC_DIR": str(tmp_path)},
    )
    lines = [ln for ln in body.splitlines() if not ln.startswith("#") and ln.strip()]

    # Exactly one sample per series - the bug exported two.
    users_lines = [ln for ln in lines if ln.startswith("sheaf_users_total")]
    assert len(users_lines) == 1, f"duplicate family exported: {users_lines}"
    assert users_lines[0].split()[-1] == "6.0"

    http_lines = [ln for ln in lines if ln.startswith("sheaf_http_requests_total{")]
    assert len(http_lines) == 1, f"duplicate family exported: {http_lines}"
    assert http_lines[0].split()[-1] == "1.0"


def test_multiproc_metric_objects_stay_out_of_scrape_registry(tmp_path):
    out = _run_child(
        """
        from sheaf.observability.registry import get_metric_registry, get_registry
        from sheaf.observability import metrics  # noqa: F401 - triggers definitions

        scrape = get_registry()
        # In multiproc mode the objects are unregistered (None) and the
        # scrape registry holds only the MultiProcessCollector.
        print(get_metric_registry() is None)
        print(len(list(scrape._collector_to_names)))
        """,
        extra_env={"PROMETHEUS_MULTIPROC_DIR": str(tmp_path)},
    )
    objects_unregistered, collector_count = out.split()
    assert objects_unregistered == "True"
    assert collector_count == "1"


def test_single_process_mode_unchanged():
    out = _run_child(
        """
        from prometheus_client import generate_latest
        from sheaf.observability.registry import get_metric_registry, get_registry
        from sheaf.observability import metrics

        assert get_metric_registry() is get_registry()
        metrics.users_total.set(3)
        body = generate_latest(get_registry()).decode()
        lines = [ln for ln in body.splitlines()
                 if ln.startswith("sheaf_users_total")]
        assert len(lines) == 1 and lines[0].split()[-1] == "3.0", lines
        print("ok")
        """,
        drop=("PROMETHEUS_MULTIPROC_DIR",),
    )
    assert out.strip() == "ok"
