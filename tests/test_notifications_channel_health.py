"""When a notification channel gives up, and what it says when it does.

Host-only: the rule is a pure predicate and the labelling is a pure mapping,
so neither needs a stack. What the stack cannot tell you anyway is the thing
that matters here, which is that the three causes of `disabled` stay distinct.

Why this exists at all: a channel that stops delivering used to leave a server
log line and nothing else. The owner's only signal was the absence of
notifications, which is indistinguishable from nothing having happened - a
bad failure mode for any feature and a particularly bad one for the feature
whose entire job is to tell you things.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sheaf.models.notification_channel import DestinationState, DisabledReason
from sheaf.services.notifications.dispatcher import has_been_failing_too_long

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_a_channel_that_is_not_failing_is_never_disabled():
    """`None` is "no current run of failures", not "failing since the epoch".

    Getting this backwards would disable every healthy channel on the first
    tick after deploy, which is the worst possible reading of a null.
    """
    assert has_been_failing_too_long(None, NOW) is False


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (timedelta(minutes=1), False),
        (timedelta(hours=1), False),
        # The case that motivated the whole change: the backoff tops out at
        # half an hour, so by here a broken destination has been retried
        # dozens of times and will go on forever unless something stops it.
        (timedelta(hours=10), False),
        (timedelta(hours=23, minutes=59), False),
        (timedelta(hours=24), True),
        (timedelta(days=3), True),
    ],
)
def test_the_rule_is_a_day_of_continuous_failure(
    elapsed: timedelta, expected: bool
):
    """Expressed in elapsed time, not in attempts.

    An attempt count only means something relative to the backoff schedule: a
    count chosen to mean "about a day" silently comes to mean "about an hour"
    the moment anyone retunes the curve. 24 attempts under the current
    schedule is roughly ten hours, which is the exact trap this avoids.
    """
    assert has_been_failing_too_long(NOW - elapsed, NOW) is expected


def test_a_clock_that_went_backwards_does_not_disable():
    """A `failing_since` in the future yields a negative delta, which must
    read as "not yet", not as an enormous elapsed time."""
    assert has_been_failing_too_long(NOW + timedelta(hours=1), NOW) is False


def test_disabled_reason_is_only_for_server_decisions():
    """The enum exists to keep three causes of `disabled` apart.

    The owner pausing a channel and the recipient unsubscribing are both
    already expressed elsewhere (`paused_by_sender` alongside
    `destination_state`), and both leave this NULL. Adding a member here for
    either of them would put the same fact in two places, and the two would
    eventually disagree.
    """
    assert [r.value for r in DisabledReason] == ["delivery_failed"]
    # And the state it accompanies is the same DISABLED the other two use:
    # this is a reason for a state, not a new state.
    assert DestinationState.DISABLED.value == "disabled"
