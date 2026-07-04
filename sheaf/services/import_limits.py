"""Business-cap clamping for importers.

Importers build ORM rows directly from uploaded files, so they bypass the
Pydantic ``max_length`` / count validation the create API enforces. Without a
guard, a crafted (or just messy) export can land a 50k-char member note or a
custom field with 10,000 choices straight in the DB. Every importer routes its
user-content fields through the helpers here so over-cap values can never be
stored - the clamp is unconditional, a security backstop that holds even if the
preview warning below is missed.

The two functions also tally what they touched into a :class:`ClampReport`. The
preview endpoints run the *same* helpers over the parsed payload (discarding the
returned values) to predict, before the import runs, exactly what would be
shortened - so the user gets a "3 member names will be shortened" warning and
can cancel or continue. Because preview and the real import call the identical
helper with the identical :class:`Cap`, the warning matches the clamp.

The caps mirror ``sheaf/schemas/*.py``. When a schema cap changes, change the
matching :class:`Cap` here too. (A model/schema/import cap-parity test is
planned as the mechanical backstop; until it lands, keep these in sync by hand.)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import NamedTuple

from sheaf.services.import_parsing import ImportPayloadError


class Cap(NamedTuple):
    """A named length/count limit. Pairing the user-facing label with the
    numeric limit means a call site can't accidentally apply one field's limit
    under another's label - the two always travel together."""

    label: str
    limit: int


# --- Member -----------------------------------------------------------------
# Mirrors sheaf/schemas/member.py.
M_NAME = Cap("member name", 100)
M_DISPLAY_NAME = Cap("member display name", 100)
M_PRONOUNS = Cap("member pronouns", 100)
M_NOTE = Cap("member note", 5000)
M_AVATAR_URL = Cap("member avatar URL", 500)
M_BANNER_URL = Cap("member banner URL", 500)
M_COLOR = Cap("member color", 7)
M_BIRTHDAY = Cap("member birthday", 10)
M_PLURALKIT_ID = Cap("member PluralKit ID", 8)
M_EMOJI = Cap("member emoji", 8)

# --- System -----------------------------------------------------------------
# Mirrors sheaf/schemas/system.py.
SYS_NAME = Cap("system name", 100)
SYS_NOTE = Cap("system note", 5000)
SYS_TAG = Cap("system tag", 8)
SYS_AVATAR_URL = Cap("system avatar URL", 500)
SYS_COLOR = Cap("system color", 7)

# --- Group / tag ------------------------------------------------------------
# Mirrors sheaf/schemas/group.py and sheaf/schemas/tag.py.
GROUP_NAME = Cap("group name", 100)
GROUP_COLOR = Cap("group color", 7)
TAG_NAME = Cap("tag name", 50)
TAG_COLOR = Cap("tag color", 7)

# --- Custom fields ----------------------------------------------------------
# Mirrors sheaf/schemas/custom_field.py (_MAX_CHOICES_PER_FIELD /
# _MAX_CHOICE_LENGTH).
CF_NAME = Cap("custom field name", 100)
CF_CHOICE = Cap("custom field choice", 100)
CF_CHOICES_COUNT = Cap("custom field's choice list", 100)

# --- Journal / message / poll / reminder ------------------------------------
JOURNAL_TITLE = Cap("journal title", 200)
MESSAGE_BODY = Cap("message", 5000)
POLL_QUESTION = Cap("poll question", 500)
POLL_DESCRIPTION = Cap("poll description", 2000)
POLL_OPTION = Cap("poll option", 200)
POLL_OPTIONS_COUNT = Cap("poll's option list", 20)
REMINDER_NAME = Cap("reminder name", 120)
REMINDER_TITLE = Cap("reminder title", 500)
REMINDER_BODY = Cap("reminder body", 2000)


@dataclass
class ClampReport:
    """Accumulates which caps a clamp pass actually hit.

    Shared currency between the import run (where it feeds the job's warning
    events) and the preview (where :meth:`to_warnings` becomes the
    ``limit_warnings`` the user sees before deciding to proceed). A report with
    nothing recorded is :attr:`empty` and produces no warnings.
    """

    _str_hits: Counter[Cap] = field(default_factory=Counter)
    _list_hits: Counter[Cap] = field(default_factory=Counter)

    def record_str(self, cap: Cap) -> None:
        self._str_hits[cap] += 1

    def record_list(self, cap: Cap) -> None:
        self._list_hits[cap] += 1

    @property
    def empty(self) -> bool:
        return not self._str_hits and not self._list_hits

    def to_warnings(self) -> list[str]:
        """Render the tally as user-facing sentences, deterministically ordered.

        One line per distinct field that was clamped, e.g.::

            3 member names will be shortened to 100 characters
            1 custom field's choice list will be trimmed to 100 entries
        """
        lines: list[str] = []
        for cap, n in sorted(self._str_hits.items(), key=lambda kv: kv[0].label):
            noun = cap.label if n == 1 else f"{cap.label}s"
            lines.append(
                f"{n} {noun} will be shortened to {cap.limit} characters"
            )
        for cap, n in sorted(self._list_hits.items(), key=lambda kv: kv[0].label):
            noun = cap.label if n == 1 else f"{cap.label}s"
            lines.append(
                f"{n} {noun} will be trimmed to {cap.limit} entries"
            )
        return lines


def clamp_str(
    value: str | None, cap: Cap, *, report: ClampReport | None = None
) -> str | None:
    """Truncate ``value`` to ``cap.limit`` characters, tallying if it had to.

    ``None`` passes through untouched. Pass a ``report`` to record the hit (the
    import run and the preview both do); omit it for a silent clamp of a field
    that isn't worth surfacing to the user.
    """
    if value is None:
        return None
    if len(value) > cap.limit:
        if report is not None:
            report.record_str(cap)
        return value[: cap.limit]
    return value


def clamp_list[T](
    items: list[T] | None, cap: Cap, *, report: ClampReport | None = None
) -> list[T] | None:
    """Cap ``items`` to ``cap.limit`` entries, tallying if it had to.

    ``None`` passes through. Keeps the leading ``cap.limit`` entries (the
    create API rejects the whole over-cap list; for an import we keep what fits
    rather than drop the record entirely).
    """
    if items is None:
        return None
    if len(items) > cap.limit:
        if report is not None:
            report.record_list(cap)
        return items[: cap.limit]
    return items


# --- Per-import row caps ----------------------------------------------------
# Hard ceilings on how many rows of each entity a SINGLE import job may create.
# Unlike the length clamps above (which shorten a value and keep going) or the
# member cap (a per-tenant product limit), these are pure resource / parse-bomb
# guards: they bound the work one crafted or oversized export can force in a
# single transaction, independent of tier or existing row counts. An over-cap
# import fails cleanly via ImportPayloadError - the same classified job failure
# as the member cap - rather than grinding through hundreds of thousands of
# per-row flushes. The values live in sheaf/config.py (import_max_*); 0 there
# disables the cap for that entity.
#
# Keys are the labels importers pass; each maps to the import_max_<key> setting
# and a user-facing plural noun. Every importer computes the per-entity count
# it would create (reusing its preview/member-cap counts where it already has
# them) and calls enforce_import_row_caps before its write loop; the preview
# surfaces the same over-cap condition up front via import_row_cap_warnings.
_ROW_CAP_LABELS: dict[str, str] = {
    "fronts": "fronts",
    "journal_entries": "journal entries",
    "messages": "messages",
    "revisions": "revisions",
    "polls": "polls",
    "groups": "groups",
    "tags": "tags",
    "custom_fields": "custom fields",
}


def _row_cap_for(entity: str) -> int:
    """Configured cap for an entity label (0 = unlimited/disabled)."""
    # Imported lazily so this module stays import-cheap and free of a
    # config dependency at load time.
    from sheaf.config import settings

    return getattr(settings, f"import_max_{entity}", 0)


def _over_cap(counts: Mapping[str, int]) -> Iterator[tuple[str, int, int]]:
    """Yield (label, count, cap) for each entity whose count exceeds its cap.

    Skips entities with a cap of 0 (disabled), a count at/under the cap, or a
    key with no configured cap, so callers only see genuine breaches.
    """
    for entity, count in counts.items():
        cap = _row_cap_for(entity)
        if cap > 0 and count > cap:
            yield _ROW_CAP_LABELS.get(entity, entity), count, cap


def _row_cap_message(label: str, count: int, cap: int) -> str:
    return (
        f"This export has {count} {label}, more than the {cap} Sheaf imports "
        "in one job. Split the file into smaller pieces, or contact support "
        "to raise the limit."
    )


def enforce_import_row_caps(counts: Mapping[str, int]) -> None:
    """Raise ImportPayloadError if any per-entity count exceeds its import cap.

    ``counts`` maps entity labels (see :data:`_ROW_CAP_LABELS`) to the number
    of rows of that entity this import would create. No-op for a cap of 0 or a
    count under cap. Raising ImportPayloadError makes an over-cap import a
    clean, classified job failure, matching the member cap. Importers call this
    once, before their write loop, with only the entities they produce.
    """
    for label, count, cap in _over_cap(counts):
        raise ImportPayloadError(_row_cap_message(label, count, cap))


def import_row_cap_warnings(counts: Mapping[str, int]) -> list[str]:
    """Non-raising preview counterpart of :func:`enforce_import_row_caps`.

    Returns one actionable warning line per over-cap entity so the preview can
    show "this export has N fronts, more than the M we import per job; split
    the file" before the user commits. The import run still enforces (raises)
    as the authoritative guard.
    """
    return [_row_cap_message(label, count, cap) for label, count, cap in _over_cap(counts)]
