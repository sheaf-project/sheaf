"""Unit tests for the email deliverability state machine.

These exercise the transition logic in `sheaf.services.email_events`
directly with a stub user and a fake DB session - no running server or
database. The transitions are the load-bearing part of the
"deliverability is a recoverable lifecycle, not a write-once-bad flag"
fix: a greylist soft bounce must not block mail, a delivery must clear
transient soft state, and a hard bounce / complaint must NOT be undone
by a later delivery (only an explicit re-verification clears those).
"""

from sheaf.models.user import EmailDeliveryStatus
from sheaf.services.email_events import (
    apply_bounce,
    apply_complaint,
    apply_delivered,
    clear_delivery_state,
)


class _StubUser:
    def __init__(self, **kw):
        self.id = "stub-user"
        self.email_delivery_status = EmailDeliveryStatus.OK
        self.email_delivery_status_changed_at = None
        self.email_soft_bounce_count = 0
        self.email_revalidation_required = False
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeResult:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeDB:
    """Stands in for AsyncSession: only `execute(...).scalar_one_or_none()`
    is touched by the event handlers; they never commit themselves."""

    def __init__(self, user):
        self._user = user

    async def execute(self, _query):
        return _FakeResult(self._user)


# --- soft-bounce threshold -------------------------------------------------


async def test_single_soft_bounce_does_not_block(monkeypatch):
    from sheaf.config import settings

    monkeypatch.setattr(settings, "email_soft_bounce_threshold", 5)
    user = _StubUser()
    db = _FakeDB(user)

    await apply_bounce(db, "x@example.com", permanent=False)

    # One greylist deferral must leave the address sendable.
    assert user.email_delivery_status == EmailDeliveryStatus.OK
    assert user.email_soft_bounce_count == 1
    assert user.email_revalidation_required is False


async def test_soft_bounce_blocks_at_threshold(monkeypatch):
    from sheaf.config import settings

    monkeypatch.setattr(settings, "email_soft_bounce_threshold", 3)
    user = _StubUser(email_soft_bounce_count=2)
    db = _FakeDB(user)

    await apply_bounce(db, "x@example.com", permanent=False)

    assert user.email_soft_bounce_count == 3
    assert user.email_delivery_status == EmailDeliveryStatus.SOFT_BOUNCING
    assert user.email_revalidation_required is True


# --- delivered self-heals soft state --------------------------------------


async def test_delivered_clears_soft_bounce(monkeypatch):
    from sheaf.config import settings

    monkeypatch.setattr(settings, "email_soft_bounce_threshold", 3)
    user = _StubUser(
        email_delivery_status=EmailDeliveryStatus.SOFT_BOUNCING,
        email_soft_bounce_count=4,
        email_revalidation_required=True,
    )
    db = _FakeDB(user)

    changed = await apply_delivered(db, "x@example.com")

    assert changed is True
    assert user.email_delivery_status == EmailDeliveryStatus.OK
    assert user.email_soft_bounce_count == 0
    assert user.email_revalidation_required is False


async def test_delivered_resets_counter_below_threshold():
    """A delivery between soft bounces zeroes the running count, so soft
    bounces must be consecutive to ever reach the threshold."""
    user = _StubUser(email_soft_bounce_count=2)
    db = _FakeDB(user)

    changed = await apply_delivered(db, "x@example.com")

    assert changed is True
    assert user.email_soft_bounce_count == 0
    assert user.email_delivery_status == EmailDeliveryStatus.OK


async def test_delivered_does_not_clear_hard_bounce():
    user = _StubUser(
        email_delivery_status=EmailDeliveryStatus.HARD_BOUNCED,
        email_revalidation_required=True,
    )
    db = _FakeDB(user)

    changed = await apply_delivered(db, "x@example.com")

    # A stray delivery must not silently undo a hard bounce.
    assert changed is False
    assert user.email_delivery_status == EmailDeliveryStatus.HARD_BOUNCED
    assert user.email_revalidation_required is True


async def test_delivered_does_not_clear_complaint():
    user = _StubUser(
        email_delivery_status=EmailDeliveryStatus.COMPLAINED,
        email_revalidation_required=True,
    )
    db = _FakeDB(user)

    changed = await apply_delivered(db, "x@example.com")

    # A complaint is an explicit "stop emailing me" - never auto-resume.
    assert changed is False
    assert user.email_delivery_status == EmailDeliveryStatus.COMPLAINED


# --- hard bounce / complaint still block immediately -----------------------


async def test_hard_bounce_blocks_and_flags():
    user = _StubUser()
    db = _FakeDB(user)

    await apply_bounce(db, "x@example.com", permanent=True)

    assert user.email_delivery_status == EmailDeliveryStatus.HARD_BOUNCED
    assert user.email_revalidation_required is True


async def test_complaint_blocks_and_flags():
    user = _StubUser()
    db = _FakeDB(user)

    await apply_complaint(db, "x@example.com")

    assert user.email_delivery_status == EmailDeliveryStatus.COMPLAINED
    assert user.email_revalidation_required is True


# --- the user-facing escape hatch ------------------------------------------


def test_clear_delivery_state_resets_everything():
    user = _StubUser(
        email_delivery_status=EmailDeliveryStatus.HARD_BOUNCED,
        email_soft_bounce_count=9,
        email_revalidation_required=True,
    )

    clear_delivery_state(user)

    assert user.email_delivery_status == EmailDeliveryStatus.OK
    assert user.email_soft_bounce_count == 0
    assert user.email_revalidation_required is False


async def test_unknown_recipient_is_noop():
    db = _FakeDB(None)
    assert await apply_bounce(db, "nobody@example.com", permanent=True) is False
    assert await apply_delivered(db, "nobody@example.com") is False
    assert await apply_complaint(db, "nobody@example.com") is False
