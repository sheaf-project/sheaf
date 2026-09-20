"""Folding a client's self-description into a bounded family.

Host-only: the parser is pure, and so is the session-name composition it sits
next to. What these protect is the cardinality contract: a stranger can put
anything in X-Sheaf-Client, and nothing they put there may ever become a
metric label or a Redis key.
"""

from __future__ import annotations

from typing import get_args

import pytest

from sheaf.auth.sessions import _parse_client_name
from sheaf.observability.client_family import (
    CLIENT_FAMILIES,
    ClientFamily,
    client_family_from,
    client_family_from_name,
)
from sheaf.observability.metrics import ClientFamilyLabel

FIREFOX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Sheaf Web/1.5.0", "web"),
        ("Sheaf Android/1.2.0", "android"),
        ("Sheaf iOS/2.0.1", "ios"),
        ("Sheaf watchOS/1.0", "watch"),
        ("Sheaf Wear/0.1", "watch"),
        # Case and surrounding whitespace are not identity.
        ("  sheaf web/1.5.0  ", "web"),
        ("SHEAF ANDROID/9", "android"),
        # Everything else is `other`, including things that are almost ours.
        ("My Custom App/0.5", "other"),
        ("Sheaf", "other"),
        ("Sheaf Web", "other"),
        ("SheafWeb/1.0", "other"),
        ("Firefox", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_header_folds_into_a_bounded_family(header: str | None, expected: str):
    family = client_family_from(header, is_api_key=False)
    assert family == expected
    assert family in CLIENT_FAMILIES


def test_the_credential_decides_api_whatever_the_header_says():
    """An API key is automation. A script that sets the web app's header is
    still a script, and a header cannot promote a key to a person."""
    assert client_family_from("Sheaf Web/1.5.0", is_api_key=True) == "api"
    assert client_family_from(None, is_api_key=True) == "api"


def test_no_user_agent_fallback_for_web():
    """A browser talking to the API is not the Sheaf web app. The parser only
    ever sees the header; a browser name reaching it (the stored session name
    of a pre-header web session) is `other`, not `web`."""
    assert client_family_from_name("Firefox") == "other"
    assert client_family_from_name("Chrome") == "other"
    assert client_family_from_name("Unknown") == "other"
    assert client_family_from_name(None) == "other"
    # A stored app name still resolves, so the sessions gauge splits correctly.
    assert client_family_from_name("Sheaf Android/1.2.0") == "android"
    assert client_family_from_name("Sheaf Web/1.5.0 (Firefox)") == "web"


def test_the_label_literal_and_the_family_tuple_agree():
    """metrics.py declares the label set for type-checking call sites and
    client_family.py declares the runtime set. If they drift, a family exists
    that the pre-warm loop never touches, or a label exists that nothing can
    produce."""
    assert set(get_args(ClientFamilyLabel)) == set(CLIENT_FAMILIES)
    assert set(get_args(ClientFamily)) == set(CLIENT_FAMILIES)


class TestSessionClientName:
    """The name a person sees in their sessions list."""

    def test_web_header_keeps_the_browser(self):
        """"Sheaf Web/1.5.0" alone tells someone less than "Firefox" did about
        which of their browsers a session belongs to. Both together tell them
        more than either."""
        assert (
            _parse_client_name(FIREFOX_UA, "Sheaf Web/1.5.0")
            == "Sheaf Web/1.5.0 (Firefox)"
        )

    def test_web_header_without_a_recognisable_browser_stays_bare(self):
        assert _parse_client_name("", "Sheaf Web/1.5.0") == "Sheaf Web/1.5.0"
        assert _parse_client_name("Something/1.0", "Sheaf Web/1.5.0") == "Sheaf Web/1.5.0"

    def test_app_headers_are_verbatim(self):
        """The phone apps' User-Agents name nothing a person would recognise,
        so nothing is appended and nothing already shown changes."""
        assert _parse_client_name("okhttp/4.12", "Sheaf Android/1.2.0") == "Sheaf Android/1.2.0"
        assert _parse_client_name(FIREFOX_UA, "Sheaf iOS/2.0") == "Sheaf iOS/2.0"

    def test_no_header_is_the_browser_parse_as_before(self):
        assert _parse_client_name(FIREFOX_UA, None) == "Firefox"
        assert _parse_client_name("", None) == "Unknown"
