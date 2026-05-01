"""Filter resolution unit tests.

These exercise the L3 > L2 > L1 algorithm directly via
`resolve_member_visibility`. The function is pure given its inputs, so we
construct in-memory model instances rather than going through the API.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from sheaf.models.system import PrivacyLevel
from sheaf.services.notifications.resolution import resolve_member_visibility


def _StubMember(*, privacy: PrivacyLevel = PrivacyLevel.PUBLIC) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), privacy=privacy)


def _make_channel(
    *,
    base_all: bool = False,
    base_include_private: bool = False,
    group_rules: list[SimpleNamespace] | None = None,
    member_rules: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        base_all_members=base_all,
        base_include_private=base_include_private,
        group_rules=group_rules or [],
        member_rules=member_rules or [],
    )


def _group_rule(
    group_id: uuid.UUID, rule: str, include_private: str = "inherit"
) -> SimpleNamespace:
    return SimpleNamespace(
        group_id=group_id, rule=rule, include_private=include_private
    )


def _member_rule(member_id: uuid.UUID, rule: str) -> SimpleNamespace:
    return SimpleNamespace(member_id=member_id, rule=rule)


def test_l3_include_overrides_everything():
    member = _StubMember(privacy=PrivacyLevel.PRIVATE)
    ch = _make_channel(member_rules=[_member_rule(member.id, "include")])
    result = resolve_member_visibility(ch, member, [])
    assert result.included is True
    assert result.attribution == "L3 rule"


def test_l3_exclude_overrides_l1():
    member = _StubMember(privacy=PrivacyLevel.PUBLIC)
    ch = _make_channel(
        base_all=True, member_rules=[_member_rule(member.id, "exclude")]
    )
    assert resolve_member_visibility(ch, member, []).included is False


def test_l2_exclude_wins_over_include():
    g_inc = uuid.uuid4()
    g_exc = uuid.uuid4()
    member = _StubMember()
    ch = _make_channel(
        base_all=True,
        group_rules=[
            _group_rule(g_inc, "include"),
            _group_rule(g_exc, "exclude"),
        ],
    )
    assert (
        resolve_member_visibility(ch, member, [g_inc, g_exc]).included is False
    )


def test_l2_include_with_yes_pulls_in_private():
    g = uuid.uuid4()
    member = _StubMember(privacy=PrivacyLevel.PRIVATE)
    ch = _make_channel(
        group_rules=[_group_rule(g, "include", include_private="yes")]
    )
    result = resolve_member_visibility(ch, member, [g])
    assert result.included is True
    assert "privacy override" in result.attribution


def test_l2_include_with_inherit_falls_through_to_l1():
    g = uuid.uuid4()
    member = _StubMember(privacy=PrivacyLevel.PRIVATE)
    # L1 says private is excluded → L2 'inherit' inherits that.
    ch = _make_channel(
        base_all=True,
        base_include_private=False,
        group_rules=[_group_rule(g, "include", include_private="inherit")],
    )
    assert resolve_member_visibility(ch, member, [g]).included is False

    # L1 includes private → 'inherit' inherits that too.
    ch.base_include_private = True
    assert resolve_member_visibility(ch, member, [g]).included is True


def test_l1_base_only():
    member = _StubMember()
    assert resolve_member_visibility(_make_channel(), member, []).included is False
    assert (
        resolve_member_visibility(
            _make_channel(base_all=True), member, []
        ).included
        is True
    )


def test_l1_private_excluded_by_default():
    member = _StubMember(privacy=PrivacyLevel.PRIVATE)
    ch = _make_channel(base_all=True, base_include_private=False)
    assert resolve_member_visibility(ch, member, []).included is False
    ch.base_include_private = True
    assert resolve_member_visibility(ch, member, []).included is True


def test_friends_treated_as_private():
    member = _StubMember(privacy=PrivacyLevel.FRIENDS)
    ch = _make_channel(base_all=True, base_include_private=False)
    assert resolve_member_visibility(ch, member, []).included is False
    ch.base_include_private = True
    assert resolve_member_visibility(ch, member, []).included is True
