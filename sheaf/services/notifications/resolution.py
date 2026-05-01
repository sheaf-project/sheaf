"""Per-member visibility resolution for a notification channel.

Implements the L3 > L2 > L1 specificity algorithm from the design doc. The
caller is responsible for prefetching the channel's group/member rules and
the candidate member's group memberships - the resolver is pure and does no
I/O of its own.

A member is "private" for filter purposes iff `Member.privacy != PUBLIC`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sheaf.models.group import Group
from sheaf.models.member import Member
from sheaf.models.notification_channel import NotificationChannel
from sheaf.models.notification_channel_group_rule import (
    GroupRuleAction,
    IncludePrivate,
    NotificationChannelGroupRule,
)
from sheaf.models.notification_channel_member_rule import MemberRuleAction
from sheaf.models.system import PrivacyLevel


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    included: bool
    attribution: str  # human-readable (e.g. "L1", "L2 Protectors", "L3 rule")


def _is_private(member: Member) -> bool:
    return member.privacy != PrivacyLevel.PUBLIC


def resolve_member_visibility(
    channel: NotificationChannel,
    member: Member,
    member_group_ids: Iterable[uuid.UUID],
    *,
    group_name_lookup: dict[uuid.UUID, str] | None = None,
) -> ResolutionResult:
    """Decide whether `member` is in `channel`'s resolved set, with attribution.

    `member_group_ids` is the set of groups the member belongs to. The caller
    prefetches this once per resolution batch to avoid N+1 lookups.

    `group_name_lookup` is optional and only used to make L2 attribution strings
    more readable (e.g. "L2 Protectors" instead of "L2 group"). Pass `None` if
    the names aren't readily available - attribution still works.
    """
    member_group_set = set(member_group_ids)

    # L3: explicit member rule always wins, even for private members.
    for m_rule in channel.member_rules:
        if m_rule.member_id == member.id:
            included = m_rule.rule == MemberRuleAction.INCLUDE.value
            return ResolutionResult(included=included, attribution="L3 rule")

    # L2: collect rules that apply to any of the member's groups.
    matching_group_rules: list[NotificationChannelGroupRule] = [
        r for r in channel.group_rules if r.group_id in member_group_set
    ]

    # Excludes win on collision within L2.
    exclude_rule = next(
        (r for r in matching_group_rules if r.rule == GroupRuleAction.EXCLUDE.value),
        None,
    )
    if exclude_rule is not None:
        return ResolutionResult(
            included=False,
            attribution=_l2_attribution(exclude_rule, group_name_lookup, "exclude"),
        )

    include_rules = [
        r for r in matching_group_rules if r.rule == GroupRuleAction.INCLUDE.value
    ]
    if include_rules:
        # Resolve include based on privacy. If member is public, IN.
        # If private, check the include_private setting; "inherit" falls
        # through to L1's base_include_private. Among multiple includes,
        # prefer the most permissive privacy treatment so a "yes" rule
        # isn't shadowed by an "inherit" rule.
        if not _is_private(member):
            return ResolutionResult(
                included=True,
                attribution=_l2_attribution(include_rules[0], group_name_lookup, "include"),
            )

        # Member is private: yes > inherit > no
        yes_rule = next(
            (r for r in include_rules if r.include_private == IncludePrivate.YES.value),
            None,
        )
        if yes_rule is not None:
            return ResolutionResult(
                included=True,
                attribution=_l2_attribution(yes_rule, group_name_lookup, "include")
                + " (privacy override)",
            )

        no_rule = next(
            (r for r in include_rules if r.include_private == IncludePrivate.NO.value),
            None,
        )
        if no_rule is not None:
            return ResolutionResult(
                included=False,
                attribution=_l2_attribution(no_rule, group_name_lookup, "include")
                + " (private excluded)",
            )

        # All include rules are 'inherit'; fall through to L1's privacy default.

    # L1: base set.
    if not channel.base_all_members:
        return ResolutionResult(included=False, attribution="L1 (not in base)")

    if not _is_private(member):
        return ResolutionResult(included=True, attribution="L1")

    if channel.base_include_private:
        return ResolutionResult(included=True, attribution="L1 (private included)")

    return ResolutionResult(included=False, attribution="L1 (private excluded)")


def _l2_attribution(
    rule: NotificationChannelGroupRule,
    group_name_lookup: dict[uuid.UUID, str] | None,
    action: str,
) -> str:
    if group_name_lookup is not None:
        name = group_name_lookup.get(rule.group_id)
        if name:
            return f"L2 {name} ({action})"
    return f"L2 group ({action})"


def build_group_name_lookup(groups: Iterable[Group]) -> dict[uuid.UUID, str]:
    """Convenience: map `group.id -> group.name` for attribution strings."""
    return {g.id: g.name for g in groups}
