"""Host-side tests for the extended metrics tier scaffolding.

Three properties, none needing a server stack:

* The client version parser is total and bounded: a recognised header
  yields `major.minor`, everything else yields `unknown`, and no header can
  produce a label value that is not two short integers.
* The day-salted token differs by day and by scope, so per-account state
  keyed on it cannot be joined across days, while the stable token the
  sketches use does not change with the day.
* The gate is real: with `METRICS_EXTENDED` off the `sheaf_ext_*` families
  are absent from the exposition, not present at zero; with it on they
  exist. Run in a child interpreter because the tier, like the registry, is
  decided at import time.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import date

import pytest

from sheaf.observability.client_family import (
    VERSION_UNKNOWN,
    client_version_from,
)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Sheaf Android/1.2.0", "1.2"),
        ("Sheaf iOS/2.10.7", "2.10"),
        ("sheaf web/1.6.0", "1.6"),
        ("Sheaf Web/1.6", "1.6"),
        ("Sheaf watchOS/0.9.1-beta", "0.9"),
        ("Sheaf Wear/03.007", "3.7"),
        ("Sheaf Android/1.2.0 (Pixel 8)", "1.2"),
        # Only the bounded prefix of the version segment survives.
        ("Sheaf Android/1.2; DROP TABLE users", "1.2"),
    ],
)
def test_recognised_headers_bucket_to_major_minor(header: str, expected: str):
    assert client_version_from(header) == expected


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Sheaf Android/",
        "Sheaf Android/latest",
        "Sheaf Android/1",
        "Sheaf Android/12345.1",
        "Sheaf Android/1.12345",
        "My Custom App/0.5",
        "Mozilla/5.0 (X11; Linux x86_64)",
    ],
)
def test_unparseable_or_foreign_headers_are_unknown(header: str | None):
    assert client_version_from(header) == VERSION_UNKNOWN


def test_version_label_is_always_two_short_integers():
    """Whatever comes in, what comes out is either `unknown` or `\\d+.\\d+` with
    each part at most four digits. That is the whole cardinality argument."""
    import re

    shape = re.compile(r"^(unknown|\d{1,4}\.\d{1,4})$")
    for header in (
        "Sheaf Android/1.2.0",
        "Sheaf iOS/9999.9999.9999",
        "Sheaf Web/00001.00002",
        "Sheaf Android/1.2.3.4.5.6",
        "Sheaf Android/1.2\n2.3",
        "Sheaf Android/1.2" + "x" * 1000,
        "Sheaf Android/" + "9" * 1000,
    ):
        assert shape.match(client_version_from(header)), header


def test_day_salted_token_changes_with_the_day_and_scope():
    from sheaf.observability.usage import _active_token, _day_salted_token

    d1, d2 = date(2026, 9, 22), date(2026, 9, 23)
    a = _day_salted_token("acct", "id-1", d1)
    assert a == _day_salted_token("acct", "id-1", d1)  # deterministic within a day
    assert a != _day_salted_token("acct", "id-1", d2)  # unjoinable across days
    assert a != _day_salted_token("sys", "id-1", d1)  # and across scopes
    assert a != _active_token("acct", "id-1")  # and never equal to the stable one
    # The stable token does not move with the day: the sketches rely on that.
    assert _active_token("acct", "id-1") == _active_token("acct", "id-1")


_GATE_PROBE = """
    from prometheus_client import generate_latest
    from sheaf.observability.registry import get_registry
    from sheaf.observability import metrics  # noqa: F401  (registers the default tier)
    from sheaf.observability import extended

    body = generate_latest(get_registry()).decode()
    print("ENABLED" if extended.ENABLED else "DISABLED")
    print("HAS_EXT" if "sheaf_ext_active_accounts_by_version" in body else "NO_EXT")
    print("HAS_DEFAULT" if "sheaf_active_accounts_daily" in body else "NO_DEFAULT")
"""


def _probe(extra_env: dict[str, str]) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k != "METRICS_EXTENDED"}
    env.update(extra_env)
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_GATE_PROBE)],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_extended_families_are_absent_when_the_tier_is_off():
    """Absent, not zero: there is nothing to alert on and nothing that could
    have leaked through a forgotten check, because the objects were never
    created."""
    assert _probe({"METRICS_ENABLED": "true"}) == ["DISABLED", "NO_EXT", "HAS_DEFAULT"]


def test_extended_families_exist_when_the_tier_is_on():
    assert _probe({"METRICS_ENABLED": "true", "METRICS_EXTENDED": "true"}) == [
        "ENABLED", "HAS_EXT", "HAS_DEFAULT",
    ]


def test_extended_stays_off_when_metrics_are_off_entirely():
    """METRICS_EXTENDED is a refinement of metrics being on, not an override."""
    assert _probe({"METRICS_ENABLED": "false", "METRICS_EXTENDED": "true"})[0] == "DISABLED"


class _FakeRedis:
    """Just enough of the read side: the day's seen-pair set."""

    def __init__(self, seen: set[str]) -> None:
        self.seen = seen

    async def sismember(self, key: str, member: str) -> bool:
        return member in self.seen

    async def scard(self, key: str) -> int:
        return len(self.seen)


class _FakePipe:
    """Records the commands the hook queues instead of executing them."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def sadd(self, *a):
        self.calls.append(("sadd", *a))

    def expire(self, *a):
        self.calls.append(("expire", *a))

    def pfadd(self, *a):
        self.calls.append(("pfadd", *a))


def _pfadd_key(pipe: _FakePipe) -> str:
    return next(c[1] for c in pipe.calls if c[0] == "pfadd")


async def test_new_versions_fold_into_other_once_the_day_is_full(monkeypatch):
    """The cap is `METRICS_EXTENDED_VERSION_PAIRS_PER_DAY`: with the day's set
    full, a version not yet seen lands under `other`, while a pair already in
    the set keeps counting under its own version. Pinned with a fake Redis so
    the property does not depend on a stack."""
    from sheaf.config import settings
    from sheaf.observability import extended

    monkeypatch.setattr(extended, "ENABLED", True)
    monkeypatch.setattr(settings, "metrics_extended_version_pairs_per_day", 2)
    day = date(2026, 9, 23)
    r = _FakeRedis({"android:1.0", "android:1.1"})

    pipe = _FakePipe()
    await extended.record_client_version(
        r, pipe, "android", "Sheaf Android/1.2.0", day, "tok"
    )
    assert _pfadd_key(pipe) == extended.version_day_key("android", "other", day)

    pipe = _FakePipe()
    await extended.record_client_version(
        r, pipe, "android", "Sheaf Android/1.1.9", day, "tok"
    )
    assert _pfadd_key(pipe) == extended.version_day_key("android", "1.1", day)

    # Below the cap a new version is recorded as itself.
    monkeypatch.setattr(settings, "metrics_extended_version_pairs_per_day", 64)
    pipe = _FakePipe()
    await extended.record_client_version(
        r, pipe, "android", "Sheaf Android/1.2.0", day, "tok"
    )
    assert _pfadd_key(pipe) == extended.version_day_key("android", "1.2", day)


async def test_hook_is_a_no_op_when_the_tier_is_off():
    from sheaf.observability import extended

    assert extended.ENABLED is False  # the unit test env never sets the flag
    pipe = _FakePipe()
    await extended.record_client_version(
        _FakeRedis(set()), pipe, "android", "Sheaf Android/1.2.0", date(2026, 9, 23), "tok"
    )
    assert pipe.calls == []
