"""Unit coverage for the shared member-default helper.

The create endpoint and five importers all route their `fronting_private`
through `default_fronting_private`, so the tri-state it implements is worth
pinning down in one fast place: the interesting case is the difference between
"the caller said nothing" and "the caller said false", which is exactly what a
re-implementation at any one call site would get wrong.
"""

from sheaf.services.member_defaults import default_fronting_private


def test_custom_front_defaults_to_guarded():
    assert default_fronting_private(is_custom_front=True) is True


def test_ordinary_member_defaults_to_unguarded():
    assert default_fronting_private(is_custom_front=False) is False


def test_explicit_false_is_honoured_on_a_custom_front():
    """A body or a file that says `false` outright means it. Creating a member
    already unguarded exposes nothing by itself - a member that has just been
    created is in no view - so it needs no gate here."""
    assert (
        default_fronting_private(is_custom_front=True, requested=False) is False
    )


def test_explicit_true_is_honoured_on_an_ordinary_member():
    assert (
        default_fronting_private(is_custom_front=False, requested=True) is True
    )


def test_none_is_the_only_thing_that_takes_the_default():
    """`None` means "the source said nothing", which is the case a flat
    `bool(x.get(key, False))` would collapse into a plain false."""
    for is_cf in (True, False):
        assert default_fronting_private(
            is_custom_front=is_cf, requested=None
        ) is default_fronting_private(is_custom_front=is_cf)
