"""Server-side defaults for member fields whose safe value depends on what
kind of member is being created.

One module because the create endpoint and every importer that can produce a
custom front have to land on the same answer. Eight copies of
``if is_custom_front: ...`` spread across the import services is exactly how
a default drifts, and a drifted default here is a privacy regression rather
than a cosmetic one.
"""

from __future__ import annotations


def default_fronting_private(
    *, is_custom_front: bool, requested: bool | None = None
) -> bool:
    """The ``fronting_private`` a newly created member should land with.

    Custom fronts ("Asleep", "Away", "Lost time") are ordinary members as far
    as the sharing model is concerned: put one in a share view and
    ``project_fronting`` names it while it is fronting, and counts it in the
    anonymous ``hidden_count`` even when it is outside the view. A public
    "Asleep" therefore broadcasts sleep state on a scrapeable URL that anyone
    can poll. So a custom front is created with the fronting guard ON, and the
    owner releases it deliberately through the gated PATCH path (step-up plus
    grace window, see ``fronting_guard_release_exposes``).

    ``requested`` is what the caller actually asked for: a bool from a request
    body or from an import file that carries the column, or ``None`` when the
    source said nothing at all. Only the ``None`` case takes the default, so
    an explicit ``false`` is still honoured - creating a member already
    unguarded exposes nothing by itself, because a member that has just been
    created is in no view yet, so there is nothing for the release gate to
    protect and it is not applied here.
    """
    if requested is not None:
        return requested
    return bool(is_custom_front)
