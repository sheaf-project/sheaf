"""The slider custom field type: its options contract and value formatting.

A slider is a position on a range. Two modes, mirroring Prism's slider field
so its exports round-trip and so the two apps mean the same thing by the
word:

* ``labeled``: 0 to 100 between a left and a right label, optionally with a
  centre label. "Soft ... Hard", "Calm ... Intense". The number is a
  position, not a quantity, and reads as a percentage.
* ``numeric``: a number between an owner-chosen min and max with an optional
  step and unit. "Height, 120 to 220 cm". The number is the quantity.

Either mode can carry a ``gradient``: two or more colours painted across the
track so the position reads at a glance. Everything here is pure: the schema
module calls `normalise_options` at the API boundary, importers call it on
whatever they assembled, and the public projection calls `format_value` so a
visitor reads "62%" or "7.5 kg" rather than a bare float.
"""

from __future__ import annotations

import math
import re
from typing import Any

MODE_LABELED = "labeled"
MODE_NUMERIC = "numeric"
MODES = (MODE_LABELED, MODE_NUMERIC)

MAX_LABEL_CHARS = 50
MAX_UNIT_CHARS = 12
MIN_GRADIENT_STOPS = 2
MAX_GRADIENT_STOPS = 8
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# The keys a stored options dict carries, always all of them, so clients can
# read any key without checking it exists.
_KEYS = ("mode", "min", "max", "step", "unit", "labels", "gradient")


def default_options() -> dict[str, Any]:
    """What a slider created with no options means: 0 to 100, no labels."""
    return {
        "mode": MODE_LABELED,
        "min": 0,
        "max": 100,
        "step": 1,
        "unit": None,
        "labels": {"left": None, "center": None, "right": None},
        "gradient": None,
    }


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _tidy_number(value: float) -> int | float:
    """3.0 stores and serialises as 3; 2.5 stays 2.5."""
    return int(value) if value == int(value) else value


def _label(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_LABEL_CHARS:
        raise ValueError(f"{name} exceeds {MAX_LABEL_CHARS} characters")
    return text


def normalise_options(raw: Any) -> dict[str, Any]:
    """Validate a slider's options and return them in canonical shape.

    None means the defaults. Unknown keys, a bad mode, min >= max, a step that
    does not fit the range, a bad colour, or too many colours are refused
    rather than quietly stored: a slider whose track cannot be drawn is a
    field nobody can set.
    """
    if raw is None:
        return default_options()
    if not isinstance(raw, dict):
        raise ValueError("options must be an object")
    unknown = set(raw) - set(_KEYS)
    if unknown:
        raise ValueError(f"options has unknown keys: {sorted(unknown)}")

    mode = raw.get("mode", MODE_LABELED)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")

    if mode == MODE_LABELED:
        # The range is the definition of the mode; a labeled slider is always
        # a percentage of the way from one label to the other.
        lo, hi = 0.0, 100.0
        step = _number(raw.get("step", 1), "step")
        unit = None
    else:
        lo = _number(raw.get("min", 0), "min")
        hi = _number(raw.get("max", 10), "max")
        step_raw = raw.get("step")
        step = _number(step_raw, "step") if step_raw is not None else None
        unit = raw.get("unit")
        if unit is not None:
            if not isinstance(unit, str):
                raise ValueError("unit must be a string")
            unit = unit.strip() or None
            if unit is not None and len(unit) > MAX_UNIT_CHARS:
                raise ValueError(f"unit exceeds {MAX_UNIT_CHARS} characters")
    if lo >= hi:
        raise ValueError("min must be less than max")
    if step is not None:
        if step <= 0:
            raise ValueError("step must be positive")
        if step > hi - lo:
            raise ValueError("step must not exceed the range")

    labels_raw = raw.get("labels") or {}
    if not isinstance(labels_raw, dict):
        raise ValueError("labels must be an object")
    if set(labels_raw) - {"left", "center", "right"}:
        raise ValueError("labels may only contain left, center and right")
    labels = {
        side: _label(labels_raw.get(side), f"labels.{side}")
        for side in ("left", "center", "right")
    }

    gradient_raw = raw.get("gradient")
    gradient: list[str] | None = None
    if gradient_raw is not None:
        if not isinstance(gradient_raw, list):
            raise ValueError("gradient must be a list of colours")
        if gradient_raw:
            if not MIN_GRADIENT_STOPS <= len(gradient_raw) <= MAX_GRADIENT_STOPS:
                raise ValueError(
                    f"gradient needs {MIN_GRADIENT_STOPS} to "
                    f"{MAX_GRADIENT_STOPS} colours"
                )
            gradient = []
            for colour in gradient_raw:
                if not isinstance(colour, str) or not _HEX_RE.match(colour.strip()):
                    raise ValueError(
                        "gradient colours must be #RRGGBB hex strings"
                    )
                gradient.append(colour.strip().upper())

    return {
        "mode": mode,
        "min": _tidy_number(lo),
        "max": _tidy_number(hi),
        "step": _tidy_number(step) if step is not None else None,
        "unit": unit,
        "labels": labels,
        "gradient": gradient,
    }


def validate_value(value: Any, options: dict[str, Any] | None) -> None:
    """Raise ValueError unless `value` is a number inside the slider's range."""
    opts = options if isinstance(options, dict) else default_options()
    number = _number(value, "slider value")
    lo = float(opts.get("min", 0))
    hi = float(opts.get("max", 100))
    if not lo <= number <= hi:
        raise ValueError(f"slider value must be between {opts['min']} and {opts['max']}")


def clamp_value(value: Any, options: dict[str, Any] | None) -> int | float | None:
    """Coerce an imported value into the range, or None if it is not a number.

    Importers use this rather than `validate_value`: a value from another app
    that is slightly outside the range (a rounding artefact, a changed max)
    is still the user's answer, so it is pinned to the nearest end rather
    than dropped.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, int | float) or not math.isfinite(value):
        return None
    opts = options if isinstance(options, dict) else default_options()
    lo = float(opts.get("min", 0))
    hi = float(opts.get("max", 100))
    return _tidy_number(min(max(float(value), lo), hi))


def format_value(value: Any, options: dict[str, Any] | None) -> str:
    """How a slider value reads as text: "62%" in labeled mode, "7.5 kg" in
    numeric mode. Used wherever a card shows the value without the track:
    the public profile, the member detail dialog."""
    opts = options if isinstance(options, dict) else default_options()
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "" if value is None else str(value)
    shown = _tidy_number(round(float(value), 2))
    if opts.get("mode", MODE_LABELED) == MODE_LABELED:
        return f"{shown}%"
    unit = opts.get("unit")
    return f"{shown} {unit}" if unit else f"{shown}"
