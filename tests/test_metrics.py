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


def test_capped_entity_volume_metrics_present():
    """The remaining bulk-creatable capped entities (board messages, polls,
    groups, tags, custom fields, reminders) each expose a label-less global
    total, a label-less per-system max, and a live-create counter, all present
    from the first scrape. The `le`-labelled per-system distribution gauges
    only appear once the distribution refresher has run, so not asserted
    here (see test_capped_entity_distributions_populate)."""
    body = _scrape()
    for name in (
        "sheaf_messages_total",
        "sheaf_system_message_count_max",
        "sheaf_messages_created_total",
        "sheaf_polls_total",
        "sheaf_system_poll_count_max",
        "sheaf_polls_created_total",
        "sheaf_open_polls_total",
        "sheaf_system_open_poll_count_max",
        "sheaf_groups_total",
        "sheaf_system_group_count_max",
        "sheaf_groups_created_total",
        "sheaf_tags_total",
        "sheaf_system_tag_count_max",
        "sheaf_tags_created_total",
        "sheaf_custom_fields_total",
        "sheaf_system_custom_field_count_max",
        "sheaf_custom_fields_created_total",
        "sheaf_reminders_total",
        "sheaf_system_reminder_count_max",
        "sheaf_reminders_created_total",
    ):
        assert _series_value(body, name) is not None, f"missing series: {name}"


def test_capped_entity_distributions_populate(admin_client: httpx.Client):
    """Triggering the hourly distribution job exercises the SQL-side
    MAX + count(*) FILTER aggregation for every capped-entity distribution
    and sets the `le`-labelled snapshot gauges. After a run each distribution
    exposes its `+Inf` bucket (total groups counted). Mirrors the front /
    journal distribution wiring, just asserted end-to-end."""
    resp = admin_client.post(
        "/v1/admin/jobs/refresh_metrics_gauge_distributions/run"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"

    body = _scrape()
    for name in (
        "sheaf_systems_by_message_count",
        "sheaf_systems_by_poll_count",
        "sheaf_systems_by_open_poll_count",
        "sheaf_systems_by_group_count",
        "sheaf_systems_by_tag_count",
        "sheaf_systems_by_custom_field_count",
        "sheaf_systems_by_reminder_count",
    ):
        val = _series_value(body, name, {"le": "+Inf"})
        assert val is not None, f"missing +Inf bucket for {name}"


# ---------------------------------------------------------------------------
# Usage (DAU / MAU) + signups
# ---------------------------------------------------------------------------

def test_signups_total_prewarmed():
    """signups_total is a label-less counter pre-warmed to zero at startup so an
    absent-series alert works from the first scrape."""
    body = _scrape()
    assert _series_value(body, "sheaf_signups_total") is not None


def test_signups_total_increments_on_registration():
    before = _scrape()
    before_val = _series_value(before, "sheaf_signups_total") or 0.0

    email = f"signup-metric-{uuid.uuid4().hex[:8]}@sheaf.dev"
    reg = httpx.post(
        f"{BASE_URL}/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery"},
        timeout=10,
    )
    assert reg.status_code in (200, 201), reg.text

    after = _scrape()
    after_val = _series_value(after, "sheaf_signups_total") or 0.0
    assert after_val >= before_val + 1


def test_usage_gauges_populate(admin_client: httpx.Client):
    """The DAU/MAU cardinality gauges are Redis-sourced (id-free HLL sketches),
    so they materialise once the slow gauge pass runs. Triggering it exposes all
    four active-* gauges (one series per auth kind: client / api / any) plus the
    public-profile adoption gauge. The only label is the bounded auth_kind; there
    is never a per-account series."""
    # The admin registration itself authenticated, so today's acct/sys sketches
    # have at least one member.
    resp = admin_client.post("/v1/admin/jobs/refresh_metrics_gauges/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"

    body = _scrape()
    # The four active-* gauges carry auth_kind; "any" is the deduped total and
    # is the series that must reflect any authenticated activity.
    for name in (
        "sheaf_active_accounts_daily",
        "sheaf_active_systems_daily",
        "sheaf_active_accounts_monthly",
        "sheaf_active_systems_monthly",
    ):
        val = _series_value(body, name, {"auth_kind": "any"})
        assert val is not None, f"missing usage gauge: {name}"
        assert val >= 0, f"{name} negative: {val}"

    adoption = _series_value(body, "sheaf_systems_with_public_profile")
    assert adoption is not None and adoption >= 0

    # DAU must be at least 1 (the admin client just authenticated via a client
    # method), and MAU is the union over the trailing window, so it can never be
    # below today's DAU.
    dau = _series_value(body, "sheaf_active_accounts_daily", {"auth_kind": "any"}) or 0.0
    mau = (
        _series_value(body, "sheaf_active_accounts_monthly", {"auth_kind": "any"})
        or 0.0
    )
    assert dau >= 1, dau
    assert mau >= dau, f"MAU ({mau}) below DAU ({dau}) - union is broken"


def test_flush_usage_sketches_job_runs(admin_client: httpx.Client):
    """The durability flush job persists the day-sketch bytes and prunes old
    rows; it must run green so MAU can survive a Redis replace."""
    resp = admin_client.post("/v1/admin/jobs/flush_usage_sketches/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"


# ---------------------------------------------------------------------------
# Public profiles / sharing
# ---------------------------------------------------------------------------

def test_sharing_counters_prewarmed():
    """The bounded-label sharing counters are pre-warmed at startup so
    absent-series alerts work from the first scrape. Spot-check one label
    value per counter."""
    body = _scrape()
    checks = [
        ("sheaf_share_grants_created_total", {"subject_type": "public"}),
        ("sheaf_share_grants_created_total", {"subject_type": "link"}),
        ("sheaf_share_grants_revoked_total", {"subject_type": "link"}),
        ("sheaf_share_grants_rotated_total", None),
        ("sheaf_share_grants_finalized_total", {"kind": "member_guard"}),
        ("sheaf_share_grants_finalized_total", {"kind": "system_privacy"}),
        ("sheaf_public_media_serves_total", {"outcome": "dark_account"}),
        ("sheaf_public_media_serves_total", {"outcome": "served"}),
        ("sheaf_share_publish_blocked_total", {"reason": "grant_cap"}),
        ("sheaf_adult_attestations_total", None),
        (
            "sheaf_watch_redemptions_total",
            {"destination_type": "mobile_push", "outcome": "auth_required"},
        ),
        (
            "sheaf_watch_redemptions_total",
            {"destination_type": "unknown", "outcome": "invalid_code"},
        ),
    ]
    for name, labels in checks:
        val = _series_value(body, name, labels)
        assert val is not None, f"missing prewarmed series: {name} {labels}"


def test_sharing_gauges_populate(admin_client: httpx.Client):
    """The point-in-time sharing gauges are DB-sourced, so they materialise
    once the slow gauge pass runs. Triggering it exposes the live-grant gauge
    (per subject type) and the oldest-pending-exposure gauge from a known
    state."""
    resp = admin_client.post("/v1/admin/jobs/refresh_metrics_gauges/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"

    body = _scrape()
    for subject_type in ("public", "link"):
        val = _series_value(
            body, "sheaf_share_grants_live", {"subject_type": subject_type}
        )
        assert val is not None, f"missing sheaf_share_grants_live {subject_type}"
    assert (
        _series_value(body, "sheaf_share_pending_exposure_oldest_seconds")
        is not None
    )


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


# ---------------------------------------------------------------------------
# Shape: feature adoption, share-view options, account age
# ---------------------------------------------------------------------------


def test_feature_adoption_and_age_gauges_populate(admin_client: httpx.Client):
    """One slow-gauge pass exposes every feature-adoption series, every
    share-view option series, and the four age buckets. The admin account
    registered moments ago, so today's under-7-days bucket has at least one
    member; and `public_profile` under the feature gauge must be the same
    number as the bare adoption gauge it aliases."""
    resp = admin_client.post("/v1/admin/jobs/refresh_metrics_gauges/run")
    assert resp.status_code == 200, resp.text
    body = _scrape()

    for feature in (
        "journals", "polls", "relationships", "reminders", "share_views",
        "custom_fields", "groups", "tags", "public_profile",
    ):
        val = _series_value(body, "sheaf_systems_with_feature", {"feature": feature})
        assert val is not None and val >= 0, feature
    assert _series_value(
        body, "sheaf_systems_with_feature", {"feature": "public_profile"}
    ) == _series_value(body, "sheaf_systems_with_public_profile")

    for option in (
        "member_permalinks", "include_fronting", "include_bio",
        "link_preview_detailed", "member_preview_detailed",
    ):
        val = _series_value(body, "sheaf_share_views_with_option", {"option": option})
        assert val is not None and val >= 0, option

    newest = _series_value(body, "sheaf_active_accounts_daily_by_age", {"account_age": "lt7d"})
    assert newest is not None and newest >= 1, newest
    for bucket in ("lt30d", "lt90d", "older"):
        val = _series_value(body, "sheaf_active_accounts_daily_by_age", {"account_age": bucket})
        assert val is not None and val >= 0, bucket


def test_share_view_distribution_populates(admin_client: httpx.Client):
    """The hourly distributions job sets the per-system share-view CDF; its
    +Inf bucket is the system count, so it is at least the admin's own."""
    resp = admin_client.post("/v1/admin/jobs/refresh_metrics_gauge_distributions/run")
    assert resp.status_code == 200, resp.text
    body = _scrape()
    total = _series_value(body, "sheaf_systems_by_share_view_count", {"le": "+Inf"})
    assert total is not None and total >= 1, total
    assert _series_value(body, "sheaf_system_share_view_count_max") is not None
# Public profiles: the demand side
# ---------------------------------------------------------------------------
#
# The test stack publishes by default, so these drive the real outcomes
# through the real routes. The property under test is the one that makes the
# counter worth having: the visitor gets ONE indistinguishable 404 for every
# refusal, and the split between those refusals exists only in the metric.


def _public_requests(body: str, surface: str, subject_type: str, outcome: str) -> float:
    return (
        _series_value(
            body,
            "sheaf_public_requests_total",
            {"surface": surface, "subject_type": subject_type, "outcome": outcome},
        )
        or 0.0
    )


def _publish(c: httpx.Client, **view_kw) -> tuple[str, str, dict]:
    """A system with a live public grant. Returns (system_id, view_id, grant)."""
    assert c.post("/v1/auth/me/attest-adult").status_code == 200
    # Visibility safety off, so the raise lands live rather than staging.
    r = c.patch("/v1/system/safety", json={"applies_to_profile_visibility": False})
    assert r.status_code == 200, r.text
    r = c.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text
    r = c.post("/v1/share-views", json={"name": f"m-{uuid.uuid4().hex[:6]}", **view_kw})
    assert r.status_code == 201, r.text
    view_id = r.json()["id"]
    r = c.post("/v1/share-grants", json={"view_id": view_id, "subject_type": "public"})
    assert r.status_code == 201, r.text
    system_id = c.get("/v1/systems/me").json()["id"]
    return system_id, view_id, r.json()["grant"]


def test_public_request_outcomes_split_behind_one_uniform_404(auth_client: httpx.Client):
    """served / withheld / not_found / dark each move their own series, while
    the three refusals are byte-identical to the visitor."""
    system_id, _, grant = _publish(auth_client, include_fronting=False)
    anon = httpx.Client(base_url=BASE_URL, timeout=5)

    before = _scrape()
    assert anon.get(f"/v1/public/systems/{system_id}").status_code == 200
    withheld = anon.get(f"/v1/public/systems/{system_id}/fronting")
    not_found = anon.get(f"/v1/public/systems/{uuid.uuid4()}")
    # Revocation is immediate; the same profile now reads as dark.
    r = auth_client.delete(f"/v1/share-grants/{grant['id']}")
    assert r.status_code in (200, 204), r.text
    dark = anon.get(f"/v1/public/systems/{system_id}")
    after = _scrape()

    # The no-oracle rule, stated as an assertion: nothing about the response
    # distinguishes the three reasons.
    for refusal in (withheld, not_found, dark):
        assert refusal.status_code == 404
        assert refusal.json() == {"detail": "Not found"}

    def delta(surface: str, outcome: str) -> float:
        return _public_requests(after, surface, "public", outcome) - _public_requests(
            before, surface, "public", outcome
        )

    assert delta("system", "served") == 1
    assert delta("fronting", "withheld") == 1
    assert delta("system", "not_found") == 1
    assert delta("system", "dark") == 1


def test_a_grant_inside_its_grace_window_counts_as_pending(auth_client: httpx.Client):
    """A pending grant 404s exactly like a missing one; the counter says which.
    Driven through a share link so the link subject type is exercised too, and
    a made-up token alongside it lands in not_found rather than dark: after a
    rotate or for a token that never existed there is genuinely no row."""
    c = auth_client
    assert c.post("/v1/auth/me/attest-adult").status_code == 200
    r = c.patch("/v1/systems/me", json={"privacy": "public"})
    assert r.status_code == 200, r.text
    r = c.patch(
        "/v1/system/safety",
        json={"grace_period_days": 7, "applies_to_profile_visibility": True, "auth_tier": "none"},
    )
    assert r.status_code == 200, r.text
    r = c.post("/v1/share-views", json={"name": f"m-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    r = c.post("/v1/share-grants", json={"view_id": r.json()["id"], "subject_type": "link"})
    assert r.status_code == 201, r.text
    assert r.json()["grant"]["status"] == "pending", r.json()
    token = r.json()["token"]
    anon = httpx.Client(base_url=BASE_URL, timeout=5)

    before = _scrape()
    pending = anon.get(f"/v1/public/shared/{token}")
    missing = anon.get(f"/v1/public/shared/{uuid.uuid4().hex}")
    after = _scrape()

    assert pending.status_code == missing.status_code == 404
    assert pending.json() == missing.json()
    for outcome in ("pending", "not_found"):
        moved = _public_requests(after, "system", "link", outcome) - _public_requests(
            before, "system", "link", outcome
        )
        assert moved == 1, (outcome, moved)


def test_link_previews_are_counted_by_card_and_unfurler(auth_client: httpx.Client):
    """A rich card and a generic one, each attributed to the service that asked;
    an unknown User-Agent is `other`; a share link is always generic."""
    system_id, _, _ = _publish(auth_client, link_preview_mode="system_details")
    discord = {"User-Agent": "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"}
    slack = {"User-Agent": "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)"}
    anon = httpx.Client(base_url=BASE_URL, timeout=5)

    def card(body: str, which: str, unfurler: str) -> float:
        return (
            _series_value(
                body, "sheaf_link_previews_total", {"card": which, "unfurler": unfurler}
            )
            or 0.0
        )

    before = _scrape()
    rich = anon.get(f"/v1/link-preview/p/{system_id}", headers=discord)
    generic_missing = anon.get(f"/v1/link-preview/p/{uuid.uuid4()}", headers=discord)
    generic_link = anon.get(f"/v1/link-preview/s/{uuid.uuid4().hex}", headers=slack)
    generic_other = anon.get(f"/v1/link-preview/p/{uuid.uuid4()}", headers={"User-Agent": "curl/8"})
    after = _scrape()

    for resp in (rich, generic_missing, generic_link, generic_other):
        assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
    assert card(after, "system_details", "discord") - card(before, "system_details", "discord") == 1
    assert card(after, "generic", "discord") - card(before, "generic", "discord") == 1
    assert card(after, "generic", "slack") - card(before, "generic", "slack") == 1
    assert card(after, "generic", "other") - card(before, "generic", "other") == 1


def test_adopters_by_subject_partition_the_total(admin_client: httpx.Client):
    """public + link + both must equal the unlabelled adopter gauge, whatever the
    data happens to be, because it is a partition and not a set of overlapping
    counts."""
    resp = admin_client.post("/v1/admin/jobs/refresh_metrics_gauges/run")
    assert resp.status_code == 200, resp.text
    body = _scrape()
    total = _series_value(body, "sheaf_systems_with_public_profile")
    assert total is not None
    parts = [
        _series_value(
            body, "sheaf_systems_with_public_profile_by_subject", {"subject_type": s}
        )
        for s in ("public", "link", "both")
    ]
    assert all(p is not None for p in parts), parts
    assert sum(parts) == total, (parts, total)


def test_public_demand_series_are_prewarmed():
    """Every bounded combination exists from the first scrape, so an
    absence-alert can fire on a series Prometheus has actually seen."""
    body = _scrape()
    assert _public_requests(body, "member", "link", "dark") >= 0
    assert (
        _series_value(
            body, "sheaf_public_requests_total",
            {"surface": "groups", "subject_type": "public", "outcome": "feature_off"},
        )
        is not None
    )
    assert (
        _series_value(
            body, "sheaf_link_previews_total", {"card": "member", "unfurler": "whatsapp"}
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Read-path backstops (2026-09-22)
# ---------------------------------------------------------------------------

def test_http_requests_total_carries_exact_status_outside_2xx():
    """A 404 and a 429 used to be the same `4xx` series. The exact code is
    now a label for anything outside 2xx; successes stay collapsed to the
    literal `2xx` so the happy path does not fan out per route."""
    before = _series_value(
        _scrape(),
        "sheaf_http_requests_total",
        {"method": "GET", "route": "/v1/members/{member_id}", "status": "404"},
    ) or 0.0
    with httpx.Client(base_url=BASE_URL) as c:
        email = f"metrics-status-{uuid.uuid4().hex[:8]}@sheaf.dev"
        r = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert r.status_code == 201
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        assert c.get(f"/v1/members/{uuid.uuid4()}").status_code == 404
        assert c.get("/v1/auth/me").status_code == 200
    body = _scrape()
    after = _series_value(
        body,
        "sheaf_http_requests_total",
        {"method": "GET", "route": "/v1/members/{member_id}", "status": "404"},
    ) or 0.0
    assert after >= before + 1
    # The success is filed under the literal "2xx", never "200".
    assert _series_value(
        body,
        "sheaf_http_requests_total",
        {"method": "GET", "route": "/v1/auth/me", "status": "2xx"},
    )
    assert _series_value(
        body,
        "sheaf_http_requests_total",
        {"method": "GET", "route": "/v1/auth/me", "status": "200"},
    ) is None


def test_pool_checkout_wait_and_concurrency_metrics_populate():
    """Every request session times its pool checkout, and every authenticated
    request takes an in-flight slot, so after one authenticated request both
    have observations. The `immediate` outcome is the normal case."""
    with httpx.Client(base_url=BASE_URL) as c:
        email = f"metrics-pool-{uuid.uuid4().hex[:8]}@sheaf.dev"
        r = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert r.status_code == 201
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        assert c.get("/v1/auth/me").status_code == 200
    body = _scrape()
    assert (_series_value(body, "sheaf_db_pool_checkout_wait_seconds_count") or 0) >= 1
    assert (
        _series_value(
            body, "sheaf_account_concurrency_total", {"outcome": "immediate"}
        ) or 0
    ) >= 1
    # Prewarmed, so the rarer outcomes exist at zero rather than being absent.
    for outcome in ("waited", "timed_out"):
        assert _series_value(
            body, "sheaf_account_concurrency_total", {"outcome": outcome}
        ) is not None
    assert (_series_value(body, "sheaf_account_concurrency_wait_seconds_count") or 0) >= 1
    # The 1.5 and 2.0 latency buckets exist.
    assert 'le="1.5"' in body and 'le="2.0"' in body


# ---------------------------------------------------------------------------
# Extended tier (METRICS_EXTENDED=true in this config)
# ---------------------------------------------------------------------------

def test_extended_version_gauge_populates(admin_client: httpx.Client):
    """The metrics config runs with the extended tier on. An authenticated
    request carrying a recognised `X-Sheaf-Client` lands in today's
    per-(family, version) sketch under the day-salted token, and the slow
    gauge pass publishes the bucketed `major.minor` count. An unparseable
    header is `unknown`, never the raw string."""
    with httpx.Client(base_url=BASE_URL) as c:
        email = f"metrics-ext-{uuid.uuid4().hex[:8]}@sheaf.dev"
        r = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert r.status_code == 201
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        assert c.get(
            "/v1/auth/me", headers={"X-Sheaf-Client": "Sheaf Android/9.9.1"}
        ).status_code == 200
        assert c.get(
            "/v1/auth/me", headers={"X-Sheaf-Client": "Sheaf iOS/not-a-version"}
        ).status_code == 200

    # The PFADD is fire-and-forget; give it a moment before the refresh reads.
    import time

    time.sleep(0.5)
    resp = admin_client.post("/v1/admin/jobs/refresh_metrics_gauges/run")
    assert resp.status_code == 200, resp.text
    body = _scrape()
    android = _series_value(
        body,
        "sheaf_ext_active_accounts_by_version",
        {"client_family": "android", "version": "9.9"},
    )
    assert android is not None and android >= 1, android
    unknown = _series_value(
        body,
        "sheaf_ext_active_accounts_by_version",
        {"client_family": "ios", "version": "unknown"},
    )
    assert unknown is not None and unknown >= 1, unknown
    # The raw header text never becomes a label value.
    assert "not-a-version" not in body
    assert "9.9.1" not in body


def test_extended_sweep_job_runs(admin_client: httpx.Client):
    resp = admin_client.post("/v1/admin/jobs/sweep_extended_metric_keys/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"
