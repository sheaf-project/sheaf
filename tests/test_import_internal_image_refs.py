"""A foreign export must not be able to name this instance's storage keys.

Sheaf keys every upload as ``{prefix}/{user_id}/{uuid}.{ext}`` and the read
path signs any internal key it is handed into a working serve URL. So a
hand-edited export from another app - a bio, a system description, a group
description, a journal body - that embeds ``/v1/files/avatars/<someone
else>/x.png`` would, once imported, be re-signed on every read of the
importing user's own profile: a live cross-tenant read of somebody else's
file, still working after they delete or un-share it.

Nothing in a PluralKit / Tupperbox / SimplyPlural / PluralSpace / Prism /
Ampersand file can legitimately point at Sheaf storage, so every internal ref
is dropped on the way in. These are the host-runnable halves (pure builders and
profile-appliers, no DB); the SimplyPlural and Ampersand walks live inside
``run_import`` and are covered end-to-end in the runner suites.
"""

from __future__ import annotations

import uuid

from sheaf.config import settings
from sheaf.models.system import System
from sheaf.services.import_limits import ClampReport
from sheaf.services.import_parsing import sanitize_external_avatar_url
from sheaf.services.members import member_description_plaintext
from sheaf.services.pk_import import build_member as pk_build_member
from sheaf.services.pluralspace_import import (
    _apply_system_profile as ps_apply_system_profile,
)
from sheaf.services.prism_import import (
    _apply_system_profile as prism_apply_system_profile,
)
from sheaf.services.tb_import import _build_member as tb_build_member

# Somebody else's account, and a key in their namespace.
_VICTIM = "22222222-2222-2222-2222-222222222222"
_FOREIGN_KEY = f"avatars/{_VICTIM}/stolen.png"
_POISON = f"Hello ![x](/v1/files/{_FOREIGN_KEY}) there"
_EXTERNAL = "![gravatar](https://gravatar.com/x.png)"


def _blank_system() -> System:
    """A System with the fields the profile-appliers read, all empty.

    They only fill blanks, so an unset description is what lets the imported
    value through at all.
    """
    return System(name="", description=None, color=None, avatar_url=None)


# --- PluralKit -------------------------------------------------------------


def test_pk_member_description_drops_internal_ref():
    member = pk_build_member(
        {"name": "Alpha", "description": _POISON},
        uuid.uuid4(),
        report=ClampReport(),
    )

    description = member_description_plaintext(member)
    assert _VICTIM not in description
    assert "/v1/files/" not in description
    # Only the embed goes; the prose around it is the user's own writing.
    assert "Hello" in description and "there" in description


def test_pk_member_description_keeps_external_image():
    member = pk_build_member(
        {"name": "Alpha", "description": _EXTERNAL},
        uuid.uuid4(),
        report=ClampReport(),
    )

    assert member_description_plaintext(member) == _EXTERNAL


def test_pk_member_description_clamped_to_cap():
    # An import must not land a description longer than the write API accepts
    # (and would then feed to the superlinear image parse). The clamp is a
    # backstop that records into the report so the preview can warn.
    report = ClampReport()
    member = pk_build_member(
        {"name": "Alpha", "description": "y" * 25000},
        uuid.uuid4(),
        report=report,
    )

    assert len(member_description_plaintext(member)) == 20000
    assert not report.empty


def test_tb_member_description_clamped_to_cap():
    report = ClampReport()
    member = tb_build_member(
        {"id": 1, "name": "Beta", "description": "y" * 25000},
        uuid.uuid4(),
        report,
    )

    assert len(member_description_plaintext(member)) == 20000
    assert not report.empty


# --- Tupperbox -------------------------------------------------------------


def test_tb_member_description_drops_internal_ref():
    member = tb_build_member(
        {"id": 1, "name": "Beta", "description": _POISON},
        uuid.uuid4(),
        ClampReport(),
    )

    description = member_description_plaintext(member)
    assert _VICTIM not in description
    assert "/v1/files/" not in description


# --- PluralSpace -----------------------------------------------------------


def test_pluralspace_system_description_drops_internal_ref():
    system = _blank_system()

    ps_apply_system_profile(
        {"system": {"name": "S", "description": _POISON}},
        system,
        ClampReport(),
    )

    assert _VICTIM not in system.description
    assert "/v1/files/" not in system.description


# --- Prism -----------------------------------------------------------------


def test_prism_system_description_drops_internal_ref():
    system = _blank_system()

    prism_apply_system_profile(
        {
            "systemSettings": [
                {"systemName": "S", "systemDescription": _POISON}
            ]
        },
        system,
        ClampReport(),
    )

    assert _VICTIM not in system.description
    assert "/v1/files/" not in system.description


# --- The shared avatar gate every foreign importer routes through ----------


def test_sanitize_external_avatar_url_rejects_cdn_form_internal_key(monkeypatch):
    """The one that gets past a scheme check.

    With a CDN hostname configured, ``https://{cdn}/avatars/{victim}/x.png`` is
    a well-formed https URL that happens to name our own storage - and
    ``resolve_avatar_url`` recognises the CDN form, so storing it would hand
    the importing profile a re-signed URL for the victim's file.
    """
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "allow_external_images", True)

    assert (
        sanitize_external_avatar_url(f"https://images.example.com/{_FOREIGN_KEY}")
        is None
    )


def test_sanitize_external_avatar_url_rejects_serve_path_and_bare_key(monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", True)

    assert sanitize_external_avatar_url(f"/v1/files/{_FOREIGN_KEY}") is None
    assert sanitize_external_avatar_url(_FOREIGN_KEY) is None


def test_sanitize_external_avatar_url_still_allows_genuine_external(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "allow_external_images", True)

    url = "https://cdn.pluralkit.example/avatars/abc.png"
    assert sanitize_external_avatar_url(url) == url
