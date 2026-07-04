"""Unit tests for the importer concurrency / nesting-depth clamps.

Pure logic, no DB and no docker stack. Two importer bypasses are covered:

* Open-poll concurrency: importers wrote Poll rows straight past the
  poll-create API's ``max_concurrent_open_for_tier`` cap. The fix keeps the
  newest incoming open polls open and flips the older excess to closed.
  ``excess_open_polls_to_close`` is the shared decision helper.
* Group nesting depth: importers set ``parent_id`` with no depth check, so an
  import could build a group tree deeper than ``MAX_GROUP_DEPTH`` (or with a
  looping parent chain). ``correct_nesting_depth`` reparents the offenders up
  to fit and cuts cycles, guaranteeing termination and a bounded depth.

The native ``preview`` group-depth prediction is exercised too, since the
warning it surfaces has to match what the run performs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sheaf.services.sheaf_import import (
    correct_nesting_depth,
    excess_open_polls_to_close,
    open_poll_import_overage,
    preview,
)

_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def _open(n: int) -> list[tuple[datetime, str]]:
    """`n` incoming open polls, oldest first (p0 oldest ... p{n-1} newest)."""
    return [(_NOW + timedelta(minutes=i), f"p{i}") for i in range(n)]


# ---------------------------------------------------------------------------
# excess_open_polls_to_close
# ---------------------------------------------------------------------------


def test_open_polls_unlimited_cap_closes_nothing():
    # cap <= 0 means the tier has no concurrent-open limit.
    assert excess_open_polls_to_close(_open(50), cap=0, existing_open=0) == []
    assert excess_open_polls_to_close(_open(50), cap=-1, existing_open=999) == []


def test_open_polls_within_cap_closes_nothing():
    assert excess_open_polls_to_close(_open(3), cap=5, existing_open=0) == []
    # exactly at the cap is still fine.
    assert excess_open_polls_to_close(_open(5), cap=5, existing_open=0) == []


def test_open_polls_excess_closes_oldest_keeps_newest():
    # 5 incoming, cap 2, none already open -> keep the 2 newest, close 3 oldest.
    to_close = excess_open_polls_to_close(_open(5), cap=2, existing_open=0)
    assert set(to_close) == {"p0", "p1", "p2"}
    assert "p3" not in to_close and "p4" not in to_close
    # Count matches the "N poll(s) ... imported as closed" warning.
    assert len(to_close) == 3


def test_open_polls_existing_open_reduces_slots():
    # cap 5, already 4 open -> only 1 slot left for 3 incoming, close 2 oldest.
    to_close = excess_open_polls_to_close(_open(3), cap=5, existing_open=4)
    assert set(to_close) == {"p0", "p1"}


def test_open_polls_cap_already_full_closes_all_incoming():
    to_close = excess_open_polls_to_close(_open(3), cap=5, existing_open=5)
    assert set(to_close) == {"p0", "p1", "p2"}
    # Over-full counts the same as full: no negative slots.
    assert set(
        excess_open_polls_to_close(_open(3), cap=5, existing_open=9)
    ) == {"p0", "p1", "p2"}


def test_open_polls_missing_source_time_ranks_oldest():
    # A poll with no source time is closed before any timestamped poll.
    created = [(None, "pN"), (_NOW, "p0"), (_NOW + timedelta(hours=1), "p1")]
    to_close = excess_open_polls_to_close(created, cap=1, existing_open=0)
    assert set(to_close) == {"pN", "p0"}
    assert "p1" not in to_close  # newest kept open


# ---------------------------------------------------------------------------
# open_poll_import_overage (pure count mirror used by the preview)
# ---------------------------------------------------------------------------


def test_overage_unlimited_cap_is_zero():
    # cap <= 0 means the tier has no concurrent-open limit.
    assert open_poll_import_overage(incoming_open=50, cap=0, existing_open=0) == 0
    assert open_poll_import_overage(incoming_open=50, cap=-1, existing_open=999) == 0


def test_overage_within_cap_is_zero():
    assert open_poll_import_overage(incoming_open=3, cap=5, existing_open=0) == 0
    # exactly at the cap is still fine.
    assert open_poll_import_overage(incoming_open=5, cap=5, existing_open=0) == 0


def test_overage_counts_existing_plus_incoming():
    # cap 5, already 4 open -> 1 slot for 3 incoming, so 2 must close.
    assert open_poll_import_overage(incoming_open=3, cap=5, existing_open=4) == 2
    # cap already full -> all incoming close.
    assert open_poll_import_overage(incoming_open=3, cap=5, existing_open=5) == 3
    # over-full counts the same as full, never more than incoming.
    assert open_poll_import_overage(incoming_open=3, cap=5, existing_open=9) == 3


def test_overage_capped_at_incoming():
    # Overage never exceeds the incoming count (existing already way over cap).
    assert open_poll_import_overage(incoming_open=2, cap=1, existing_open=100) == 2


def test_overage_matches_list_helper():
    # The pure count must equal what the list decision helper would close.
    for incoming, cap, existing in [
        (5, 2, 0),
        (3, 5, 4),
        (3, 5, 5),
        (3, 5, 9),
        (0, 5, 2),
        (4, 0, 0),
    ]:
        closed = excess_open_polls_to_close(
            _open(incoming), cap=cap, existing_open=existing
        )
        assert open_poll_import_overage(
            incoming_open=incoming, cap=cap, existing_open=existing
        ) == len(closed)


# ---------------------------------------------------------------------------
# correct_nesting_depth
# ---------------------------------------------------------------------------


def _depths(parent_of: dict) -> dict:
    """Depth of every node (root = 1) from a corrected, acyclic parent map."""
    out: dict = {}

    def depth(n):
        if n in out:
            return out[n]
        p = parent_of.get(n)
        out[n] = 1 if (p is None or p not in parent_of) else depth(p) + 1
        return out[n]

    return {n: depth(n) for n in parent_of}


def test_depth_shallow_tree_untouched():
    parent_of = {"a": None, "b": "a", "c": "b"}
    result = correct_nesting_depth(parent_of, max_depth=8)
    assert result.moved == set()
    assert result.cycle_broken == set()
    assert result.parent_of == parent_of


def test_depth_too_deep_chain_reparented_within_cap():
    # g1 (root) .. g10, each the child of the previous -> depths 1..10.
    parent_of = {"g1": None}
    for i in range(2, 11):
        parent_of[f"g{i}"] = f"g{i - 1}"
    result = correct_nesting_depth(parent_of, max_depth=8)

    # Nothing exceeds the cap after correction.
    assert max(_depths(result.parent_of).values()) <= 8
    # The two nodes past depth 8 were the ones moved.
    assert result.moved == {"g9", "g10"}
    assert result.cycle_broken == set()
    # ...and they land at exactly the deepest allowed level.
    depths = _depths(result.parent_of)
    assert depths["g9"] == 8 and depths["g10"] == 8


def test_depth_subtree_moves_and_stays_bounded():
    # A too-deep node with its own descendant: both must end <= max_depth.
    parent_of = {"r": None}
    chain = ["r"]
    for i in range(1, 12):
        node = f"n{i}"
        parent_of[node] = chain[-1]
        chain.append(node)
    result = correct_nesting_depth(parent_of, max_depth=8)
    assert max(_depths(result.parent_of).values()) <= 8


def test_depth_cycle_is_broken_and_terminates():
    # A 3-node parent cycle must not hang and must end within the cap.
    parent_of = {"a": "b", "b": "c", "c": "a"}
    result = correct_nesting_depth(parent_of, max_depth=8)
    assert result.cycle_broken  # at least one node rooted to cut the loop
    assert max(_depths(result.parent_of).values()) <= 8


def test_depth_self_loop_is_broken():
    parent_of = {"a": "a"}
    result = correct_nesting_depth(parent_of, max_depth=8)
    assert result.cycle_broken == {"a"}
    assert result.parent_of["a"] is None


def test_depth_cycle_with_deep_tail_bounded():
    # Cycle a<->b, plus a long tail hanging off b: everything ends bounded,
    # and the call returns (the guard against a crafted cyclic parent set).
    parent_of = {"a": "b", "b": "a"}
    prev = "b"
    for i in range(1, 15):
        parent_of[f"t{i}"] = prev
        prev = f"t{i}"
    result = correct_nesting_depth(parent_of, max_depth=8)
    assert max(_depths(result.parent_of).values()) <= 8


# ---------------------------------------------------------------------------
# native preview group-depth prediction
# ---------------------------------------------------------------------------


def _deep_group_payload(n: int) -> dict:
    groups = [
        {
            "id": f"g{i}",
            "name": f"G{i}",
            "parent_id": f"g{i - 1}" if i > 1 else None,
        }
        for i in range(1, n + 1)
    ]
    return {"version": "2", "system": {"name": "Deep"}, "groups": groups}


def test_preview_flags_over_depth_groups():
    summary = preview(_deep_group_payload(10))
    assert any(
        "maximum nesting depth (8)" in w for w in summary.limit_warnings
    ), summary.limit_warnings
    # Two groups (depths 9 and 10) get moved up to fit.
    assert any(
        w.startswith("2 group(s) exceed") for w in summary.limit_warnings
    ), summary.limit_warnings


def test_preview_shallow_groups_have_no_depth_warning():
    summary = preview(_deep_group_payload(5))
    assert not any(
        "nesting depth" in w for w in summary.limit_warnings
    ), summary.limit_warnings


def test_preview_flags_cyclic_group_parents():
    payload = {
        "version": "2",
        "system": {"name": "Loop"},
        "groups": [
            {"id": "a", "name": "A", "parent_id": "b"},
            {"id": "b", "name": "B", "parent_id": "a"},
        ],
    }
    summary = preview(payload)  # must not hang
    assert any(
        "looping parent reference" in w for w in summary.limit_warnings
    ), summary.limit_warnings
