"""End-to-end tests for the /metrics endpoint and instrumentation hooks.

These tests assume the test stack has been brought up with metrics
enabled and mounted on the main listener (METRICS_BIND=main +
METRICS_TOKEN set). The dedicated metrics row in run_tests.sh exercises
this configuration. Other configs leave metrics off, so the suite skips
out cleanly.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8001")
METRICS_TOKEN = os.environ.get("SHEAF_TEST_METRICS_TOKEN", "")
METRICS_ENABLED = bool(METRICS_TOKEN)


pytestmark = pytest.mark.skipif(
    not METRICS_ENABLED,
    reason="requires SHEAF_TEST_METRICS_TOKEN (set by the metrics test config)",
)


def _scrape() -> str:
    """Fetch /metrics and return the raw text body."""
    headers = {"Authorization": f"Bearer {METRICS_TOKEN}"}
    r = httpx.get(f"{BASE_URL}/metrics", headers=headers, timeout=5)
    r.raise_for_status()
    return r.text


def _series_value(body: str, name: str, labels: dict[str, str] | None = None) -> float | None:
    """Extract a counter / gauge value from a scrape body.

    Naive parser: skips comments and finds the first line whose name
    matches and whose label set is a superset of `labels`. Returns
    None when not found.
    """
    target_labels = labels or {}
    prefix = name + "{"
    bare = name + " "
    for line in body.splitlines():
        if line.startswith("#"):
            continue
        if not (line.startswith(prefix) or line.startswith(bare)):
            continue
        if line.startswith(bare):
            if not target_labels:
                # bare value: "name VALUE"
                try:
                    return float(line.split()[-1])
                except ValueError:
                    return None
            continue
        label_str, _, val_str = line.rpartition(" ")
        labels_part = label_str[len(prefix):-1]  # strip "name{" ... "}"
        # tokenise: key="val",key="val"
        parsed: dict[str, str] = {}
        for token in labels_part.split(","):
            if "=" not in token:
                continue
            k, _, v = token.partition("=")
            parsed[k.strip()] = v.strip().strip('"')
        if all(parsed.get(k) == v for k, v in target_labels.items()):
            try:
                return float(val_str)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Endpoint shape
# ---------------------------------------------------------------------------

def test_metrics_endpoint_requires_token():
    r = httpx.get(f"{BASE_URL}/metrics", timeout=5)
    assert r.status_code == 401


def test_metrics_endpoint_rejects_wrong_token():
    r = httpx.get(
        f"{BASE_URL}/metrics",
        headers={"Authorization": "Bearer wrong"},
        timeout=5,
    )
    assert r.status_code == 401


def test_metrics_endpoint_returns_text_exposition():
    body = _scrape()
    assert "# HELP" in body
    assert "sheaf_build_info" in body


def test_build_info_gauge_set():
    body = _scrape()
    # build_info is always 1; labels carry version + sheaf_mode + git_commit.
    # We can't predict the version string so check the presence of the line.
    assert any(
        line.startswith("sheaf_build_info{")
        for line in body.splitlines()
    )


# ---------------------------------------------------------------------------
# HTTP RED middleware
# ---------------------------------------------------------------------------

def test_http_requests_total_increments():
    # /v1/auth/config is public + cheap; a known stable route template.
    before = _scrape()
    before_val = _series_value(
        before,
        "sheaf_http_requests_total",
        {"method": "GET", "route": "/v1/auth/config", "status_class": "2xx"},
    ) or 0.0

    for _ in range(3):
        r = httpx.get(f"{BASE_URL}/v1/auth/config", timeout=5)
        assert r.status_code == 200

    after = _scrape()
    after_val = _series_value(
        after,
        "sheaf_http_requests_total",
        {"method": "GET", "route": "/v1/auth/config", "status_class": "2xx"},
    ) or 0.0
    assert after_val >= before_val + 3


# ---------------------------------------------------------------------------
# Auth funnel
# ---------------------------------------------------------------------------

def test_login_funnel_user_not_found_increments():
    before = _scrape()
    before_val = _series_value(
        before, "sheaf_auth_logins_total", {"outcome": "user_not_found"},
    ) or 0.0

    # Submitting a guaranteed-nonexistent address. Body shape matches the
    # UserLogin schema (see sheaf/schemas/user.py).
    payload = {"email": f"ghost-{uuid.uuid4().hex[:8]}@sheaf.dev", "password": "wrong-pw"}
    r = httpx.post(f"{BASE_URL}/v1/auth/login", json=payload, timeout=5)
    assert r.status_code in (401, 423)  # generic auth failure

    after = _scrape()
    after_val = _series_value(
        after, "sheaf_auth_logins_total", {"outcome": "user_not_found"},
    ) or 0.0
    assert after_val >= before_val + 1


def test_login_funnel_password_incorrect_distinct_from_user_not_found():
    # Register a fresh account, then submit the wrong password and
    # confirm the password_incorrect outcome — not user_not_found —
    # increments.
    email = f"login-funnel-{uuid.uuid4().hex[:8]}@sheaf.dev"
    reg = httpx.post(
        f"{BASE_URL}/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery"},
        timeout=10,
    )
    assert reg.status_code in (200, 201)

    before = _scrape()
    bad_pw_before = _series_value(
        before, "sheaf_auth_logins_total", {"outcome": "password_incorrect"},
    ) or 0.0

    r = httpx.post(
        f"{BASE_URL}/v1/auth/login",
        json={"email": email, "password": "not-the-password"},
        timeout=5,
    )
    assert r.status_code == 401

    after = _scrape()
    bad_pw_after = _series_value(
        after, "sheaf_auth_logins_total", {"outcome": "password_incorrect"},
    ) or 0.0
    assert bad_pw_after >= bad_pw_before + 1


# ---------------------------------------------------------------------------
# Pre-warmed counters
# ---------------------------------------------------------------------------

def test_decrypt_failures_total_prewarmed_to_zero():
    # Should always be zero; the prewarm ensures the series exists from
    # the first scrape so an absence-alert can fire on a non-zero rate.
    body = _scrape()
    val = _series_value(
        body, "sheaf_decrypt_failures_total", {"field": "email"},
    )
    assert val is not None and val == 0.0


def test_webhook_signature_failures_total_prewarmed():
    body = _scrape()
    for endpoint in ("sendgrid", "cf_shield", "notification_dispatch"):
        val = _series_value(
            body, "sheaf_webhook_signature_failures_total", {"endpoint": endpoint},
        )
        assert val is not None, f"missing prewarmed series for endpoint={endpoint}"


def test_front_volume_metrics_present():
    """The front-history volume metrics (for the retention decision) are
    label-less and so are exposed from the first scrape: fronts_total and
    system_front_count_max as gauges, fronts_created_total as a counter.
    The per-system distribution gauge (sheaf_systems_by_front_count) is
    labelled by `le` and only appears once the gauge refresher has run, so
    it is not asserted here."""
    body = _scrape()
    for name in (
        "sheaf_fronts_total",
        "sheaf_system_front_count_max",
        "sheaf_fronts_created_total",
    ):
        assert _series_value(body, name) is not None, f"missing series: {name}"


def test_journal_revision_volume_metrics_present():
    """The journal-entry and content-revision volume metrics (for the
    journal-revision cap decision) are label-less and exposed from the
    first scrape. The per-system / per-target distribution gauges are `le`-
    labelled and only appear once the refresher has run, so not asserted."""
    body = _scrape()
    for name in (
        "sheaf_journal_entries_total",
        "sheaf_system_journal_entry_count_max",
        "sheaf_content_revisions_total",
        "sheaf_target_revision_count_max",
        "sheaf_content_revisions_created_total",
    ):
        assert _series_value(body, name) is not None, f"missing series: {name}"


# ---------------------------------------------------------------------------
# Leader election
# ---------------------------------------------------------------------------

def test_leader_is_leader_gauge_is_one():
    """The test stack is a single process with leader election on, so it
    holds leadership: sheaf_leader_is_leader must be present and 1.

    In prod, the alert is sum(sheaf_leader_is_leader) != 1; this is the
    single-node analogue."""
    body = _scrape()
    val = _series_value(body, "sheaf_leader_is_leader")
    assert val is not None, "sheaf_leader_is_leader missing from scrape"
    assert val == 1.0, f"expected sole leader to report 1, got {val}"


def test_leader_transitions_total_present():
    body = _scrape()
    val = _series_value(body, "sheaf_leader_transitions_total")
    # At least one acquisition happened at startup.
    assert val is not None and val >= 1.0, val
