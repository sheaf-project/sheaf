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
    privacy: PrivacyLevel = PrivacyLevel.PRIVATE

    @model_validator(mode="after")
    def _validate_options(self) -> "CustomFieldCreate":
        self.options = _validate_options_for_type(self.field_type, self.options)
        return self


class CustomFieldUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    options: dict | None = None
    order: int | None = None
    privacy: PrivacyLevel | None = None

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
    value: Any


class CustomFieldValueRead(BaseModel):
    field_id: uuid.UUID
    member_id: uuid.UUID
    value: Any

    model_config = {"from_attributes": True}
