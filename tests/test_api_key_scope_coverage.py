"""Guards against API-key scope wiring drift.

The bug this prevents: an endpoint requires a scope (via require_scope, either
per-endpoint or at router-mount time) that isn't in the grantable set
(_VALID_SCOPES). No API key can ever hold it, so the endpoint is permanently
403 for key auth - which is exactly how polls/journals/messages broke.

Static source scan rather than app introspection: require_scope returns a
closure whose captured scope string isn't readily inspectable, and a grep is
both simpler and catches the router-mount deps too.
"""

from __future__ import annotations

import re
from pathlib import Path

from sheaf.api.v1.auth import _VALID_SCOPES

_API_DIR = Path(__file__).resolve().parent.parent / "sheaf" / "api" / "v1"
_SCOPE_RE = re.compile(r'require_scope\(\s*"([^"]+)"\s*\)')


def _required_scopes() -> set[str]:
    found: set[str] = set()
    for path in _API_DIR.glob("*.py"):
        found |= set(_SCOPE_RE.findall(path.read_text()))
    return found


def test_every_required_scope_is_grantable():
    """Each scope the API enforces must be one a key can actually be granted."""
    required = _required_scopes()
    assert required, "no require_scope() calls found - scan likely broken"
    missing = required - _VALID_SCOPES
    assert not missing, (
        "endpoints require scopes that aren't in _VALID_SCOPES, so no API key "
        f"can ever satisfy them: {sorted(missing)}"
    )


def test_polls_and_messages_scopes_present():
    """Regression anchors for the two that were missing."""
    for scope in (
        "polls:read",
        "polls:write",
        "polls:delete",
        "messages:read",
        "messages:write",
        "messages:delete",
    ):
        assert scope in _VALID_SCOPES, scope
