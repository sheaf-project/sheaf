import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from sheaf.models.custom_field import FieldType
from sheaf.models.system import PrivacyLevel

# Choices are a list of distinct, non-empty, trimmed strings. Mobile
# clients currently pass options=null for select/multiselect (the
# choices editor is web-side for now); when choices are absent the
# value validator skips constraint checks and any string / list is
# accepted, so the type still behaves like a freeform tag input.
_MAX_CHOICES_PER_FIELD = 100
_MAX_CHOICE_LENGTH = 100

# Cap on a stored field VALUE's text. Until this existed, a value was the one
# piece of user content the API took with no length at all: the validator below
# checked the value's TYPE and nothing else, so a text field would accept a
# string of any size, encrypt it, and store it - and then decrypt it on every
# read of that member, including on a public profile. 20000 is the same number
# every other long-form field on the instance uses (member/system/group
# descriptions, `max_length=20000` in the schemas next door), which is the
# right ceiling here: a custom field is a short labelled answer, so a limit
# generous enough for a whole bio cannot get in a real user's way.
#
# The cap bounds NEW text; it does not retroactively invalidate what was stored
# before it existed. Nothing rewrites or truncates an existing row, and the
# write path re-accepts an over-cap value that comes back unchanged (see
# api/v1/custom_fields.set_member_field_values), so somebody carrying a long
# value from before is not locked out of editing their other fields.
MAX_CUSTOM_FIELD_VALUE_CHARS = 20000


def _normalise_choices(raw: list) -> list[str]:
    """Trim, drop empties, enforce length cap + case-insensitive uniqueness.

    Preserves the caller's display order. Raises ValueError on bad input;
    the create/update schemas surface this as a 422.
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("every choice must be a string")
        text = item.strip()
        if not text:
            continue
        if len(text) > _MAX_CHOICE_LENGTH:
            raise ValueError(
                f"choice text exceeds {_MAX_CHOICE_LENGTH} chars"
            )
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    if len(out) > _MAX_CHOICES_PER_FIELD:
        raise ValueError(
            f"at most {_MAX_CHOICES_PER_FIELD} choices per field"
        )
    if not out:
        raise ValueError("choices list cannot be empty")
    return out


def _validate_options_for_type(
    field_type: FieldType, options: dict | None
) -> dict | None:
    """Normalise the per-field-type options dict.

    For SELECT / MULTISELECT, when `options.choices` is supplied it gets
    normalised (trimmed, deduped, capped) and stored back. When
    `options` is None we leave it as-is — these are the mobile clients'
    "freeform tag" mode where any string is accepted.

    For the other field types `options` must be None: those types
    don't carry options today.
    """
    if field_type in (FieldType.SELECT, FieldType.MULTISELECT):
        if options is None:
            return None
        if not isinstance(options, dict):
            raise ValueError("options must be an object")
        if set(options) - {"choices"}:
            raise ValueError("options may only contain 'choices'")
        raw = options.get("choices")
        if raw is None:
            return None
        if not isinstance(raw, list):
            raise ValueError("options.choices must be a list")
        return {"choices": _normalise_choices(raw)}

    if options:
        raise ValueError(
            f"options is only supported for select/multiselect, not {field_type}"
        )
    return None


class CustomFieldCreate(BaseModel):
    name: str = Field(max_length=100)
    field_type: FieldType
    options: dict | None = None
    order: int = 0
    # Born private unless asked otherwise, and asking otherwise runs the same
    # gate the PATCH raise does (see api/v1/custom_fields.create_field):
    # creating a field already public and raising an existing one are the same
    # exposure, so "delete it and add it back as public" must not be a way
    # round the slower door.
    privacy: PrivacyLevel = PrivacyLevel.PRIVATE
    # Step-up credentials, carried for the same reason `GroupCreate` carries
    # them: a create that skips straight to public goes through the same door
    # as a raise. Popped before persistence.
    password: str | None = Field(
        default=None, description="Required when the change is deferred"
    )
    totp_code: str | None = None

    @model_validator(mode="after")
    def _validate_options(self) -> "CustomFieldCreate":
        self.options = _validate_options_for_type(self.field_type, self.options)
        return self


class CustomFieldUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    options: dict | None = None
    order: int | None = None
    privacy: PrivacyLevel | None = None

    # Step-up credentials for a raise that is actually deferred. NOT field
    # columns: the handler pops them before anything iterates the update, the
    # same way the group and relationship-edge handlers do, so they can never
    # be persisted.
    password: str | None = Field(
        default=None, description="Required when the change is deferred"
    )
    totp_code: str | None = None

    @field_validator("name", "order", "privacy")
    @classmethod
    def _reject_explicit_null(cls, v):
        if v is None:
            raise ValueError("cannot be null")
        return v


class CustomFieldRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str
    field_type: FieldType
    options: dict | None
    order: int
    privacy: PrivacyLevel
    # A raise waiting out the grace window: `privacy` above is still the truth,
    # and this says what it will become when `privacy_activates_at` passes.
    # Null = nothing staged.
    pending_privacy: PrivacyLevel | None = None
    privacy_activates_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # finalize_after timestamp if queued for delete; null otherwise.
    pending_delete_at: datetime | None = None

    model_config = {"from_attributes": True}


def _unwrap_value(raw: Any) -> Any:
    """Strip the legacy `{v: ...}` envelope so validators see the raw value.

    The web client wraps submitted values as `{"v": <scalar>}` for
    historical reasons; iOS / Android send raw scalars. Validation
    should accept either - we unwrap once at the boundary.
    """
    if (
        isinstance(raw, dict)
        and set(raw) == {"v"}
    ):
        return raw["v"]
    return raw


def value_over_text_cap(value: Any) -> bool:
    """True if any string inside a submitted/stored value is over the cap.

    THE definition of "too long", shared by the write API and the importers so
    the two cannot disagree about which values are refused. Takes the value in
    any shape it legitimately arrives in: a bare scalar, the web client's
    legacy `{"v": ...}` envelope, or a multiselect's list (walked so an
    oversized string cannot ride in as a list entry). Non-string leaves have no
    length and are never over.

    Deliberately a predicate rather than a raise: the API turns it into a 422
    and an importer turns it into a skipped value with a warning, and neither
    reading belongs in here.
    """
    unwrapped = _unwrap_value(value)
    items = unwrapped if isinstance(unwrapped, list) else [unwrapped]
    return any(
        isinstance(item, str) and len(item) > MAX_CUSTOM_FIELD_VALUE_CHARS
        for item in items
    )


def _validate_value_for_field(
    field_type: FieldType,
    options: dict | None,
    value: Any,
) -> None:
    """Raise ValueError if `value` doesn't match the field's type contract.

    Only enforces what's structurally checkable here:
      - SELECT: when choices are set, value (unwrapped) must be one of
        them. When choices are absent, any string is accepted.
      - MULTISELECT: when choices are set, value (unwrapped) must be a
        list whose entries are all in choices, no duplicates. Empty
        list allowed (clears the selection).
      - Other types: anything serialisable goes through. The web /
        mobile widgets enforce shape client-side.

    Length is NOT checked here, and cannot be: an over-cap value that is
    already stored has to stay editable (see `value_over_text_cap` and the
    write handler), and a validator with no database in front of it cannot
    tell "somebody is pasting a novel" from "somebody is re-saving what they
    already had".
    """
    if value is None:
        return  # nullable; clear-on-save.
    unwrapped = _unwrap_value(value)
    choices: list[str] | None = (
        options.get("choices") if isinstance(options, dict) else None
    )
    if field_type is FieldType.SELECT:
        if not isinstance(unwrapped, str):
            raise ValueError("select value must be a string")
        if choices is not None and unwrapped not in choices:
            raise ValueError(
                f"'{unwrapped}' is not one of the defined choices"
            )
    elif field_type is FieldType.MULTISELECT:
        if not isinstance(unwrapped, list):
            raise ValueError("multiselect value must be a list of strings")
        seen: set[str] = set()
        for item in unwrapped:
            if not isinstance(item, str):
                raise ValueError(
                    "multiselect entries must all be strings"
                )
            if item in seen:
                raise ValueError(
                    f"multiselect entry '{item}' appears more than once"
                )
            seen.add(item)
            if choices is not None and item not in choices:
                raise ValueError(
                    f"'{item}' is not one of the defined choices"
                )


class CustomFieldValueSet(BaseModel):
    field_id: uuid.UUID
    # Defaulted so an entry that OMITS `value` clears the field exactly like an
    # explicit null. Absence has no other meaning on this endpoint (the handler
    # upserts every entry it is given; there is no omit-to-leave-alone mode),
    # and several client serialisers drop null object fields by default - Moshi
    # on Android serialised "clear this field" as {"field_id": ...} and the
    # whole request 422'd. Without the default, `Any` is a required field in
    # Pydantic v2.
    value: Any = None


class CustomFieldValueRead(BaseModel):
    field_id: uuid.UUID
    member_id: uuid.UUID
    value: Any

    model_config = {"from_attributes": True}
