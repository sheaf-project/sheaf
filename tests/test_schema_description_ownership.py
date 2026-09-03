"""Every long-form description field is normalised and bounded the same way.

Member bios, system descriptions and group descriptions are all markdown that
renders image embeds, so all three must route through ``normalize_description_urls``
on write (our own refs canonicalised to ``/v1/files/{key}`` with signed params
stripped, disallowed externals dropped) and all three must carry a length cap so
the superlinear image parse can't be handed an unbounded body. Group descriptions
were historically the one field missing the normaliser; these tests pin the
parity so a future edit can't quietly drop it from one schema.

Pure Pydantic construction, no DB - host-runnable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sheaf.config import settings
from sheaf.schemas.group import GroupCreate, GroupUpdate
from sheaf.schemas.member import MemberCreate, MemberUpdate
from sheaf.schemas.system import SystemCreate, SystemUpdate

# A hosted image ref carrying a signed query param, in the /v1/files form the
# client round-trips. The normaliser must canonicalise it and drop the token.
_SIGNED_HOSTED = "before ![pic](/v1/files/avatars/abc/x.png?token=deadbeef) after"
_EXTERNAL = "look ![p](https://gravatar.example/x.png) here"

# One representative create + update schema per entity, each keyed by the extra
# fields their constructor needs beyond `description`.
_CREATE_CASES = [
    ("member", MemberCreate, {"name": "A"}),
    ("system", SystemCreate, {"name": "A"}),
    ("group", GroupCreate, {"name": "A"}),
]
_UPDATE_CASES = [
    ("member", MemberUpdate, {}),
    ("system", SystemUpdate, {}),
    ("group", GroupUpdate, {}),
]


@pytest.mark.parametrize("label,model,extra", _CREATE_CASES + _UPDATE_CASES)
def test_description_canonicalises_hosted_ref(label, model, extra):
    obj = model(description=_SIGNED_HOSTED, **extra)
    # Canonicalised to the bare serve path, signed token stripped - identical
    # treatment across member / system / group.
    assert obj.description is not None
    assert "/v1/files/avatars/abc/x.png" in obj.description
    assert "token=" not in obj.description
    assert "before" in obj.description and "after" in obj.description


@pytest.mark.parametrize("label,model,extra", _CREATE_CASES + _UPDATE_CASES)
def test_description_drops_external_when_policy_off(label, model, extra, monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", False)
    obj = model(description=_EXTERNAL, **extra)
    # The external embed is stripped from the text; the prose stays.
    assert obj.description is not None
    assert "gravatar.example" not in obj.description
    assert "look" in obj.description and "here" in obj.description


@pytest.mark.parametrize("label,model,extra", _CREATE_CASES + _UPDATE_CASES)
def test_description_rejects_over_cap(label, model, extra):
    # 20k is the shared cap; one char past it is a 422, not an unbounded parse.
    with pytest.raises(ValidationError):
        model(description="x" * 20001, **extra)


@pytest.mark.parametrize("label,model,extra", _CREATE_CASES + _UPDATE_CASES)
def test_description_allows_at_cap(label, model, extra):
    obj = model(description="x" * 20000, **extra)
    assert obj.description is not None and len(obj.description) == 20000
