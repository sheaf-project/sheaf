"""Public projection schemas: the fail-closed contract for the anonymous surface.

These are built field-by-field from plaintext the projection code explicitly
chooses (see sheaf/services/share_projection.py). They are NEVER
`model_validate`d over an ORM row, so a column added to a model can never drift
into a public payload by accident. The `test_public_profiles_api.py` key-set
snapshot tests pin the exact field set of each of these as the contract.

Nothing encrypted-at-rest appears here except the two fields a member may
deliberately publish: their bio (only when the view includes it) and their
custom-field values (only for fields the view exposes). Notes, journals, front
history, settings, account/tier data, and everything else stay off this surface
by simply not being listed.
"""

from pydantic import BaseModel


class PublicMemberView(BaseModel):
    # Identity always shown for a member the owner put in a view.
    id: str
    name: str
    display_name: str | None = None
    pronouns: str | None = None
    avatar_url: str | None = None
    banner_url: str | None = None
    color: str | None = None
    # Only populated when the view has include_bio on. Markdown, with embedded
    # image refs resolved/signed like any other rendered bio.
    bio: str | None = None
    # {field_name: value} for the custom fields this view exposes and that this
    # member has a value for. Empty when the view exposes no fields.
    fields: dict[str, object] = {}


class PublicSystemView(BaseModel):
    id: str
    name: str
    description: str | None = None
    avatar_url: str | None = None
    color: str | None = None
    tag: str | None = None
    # Count of members actually visible in this view (after the hard guards),
    # not the system's real member count. Deliberately no created_at: the age
    # of a system is mildly sensitive and worthless to a visitor.
    member_count: int


class PublicFrontingMember(BaseModel):
    """A lite identity card for a currently-fronting member.

    Deliberately NOT a PublicMemberView: the "who is fronting right now" surface
    is the sharpest one (real-time presence, polled repeatedly), so it carries
    only identity + when the front started. Bio and custom fields, even when the
    view exposes them, are reached through the /members endpoint, not handed out
    on every fronting poll.
    """

    id: str
    name: str
    display_name: str | None = None
    pronouns: str | None = None
    avatar_url: str | None = None
    color: str | None = None
    # When this member's current front started. None if unknown.
    since: str | None = None


class PublicFrontingView(BaseModel):
    # Publicly-visible members currently fronting.
    members: list[PublicFrontingMember] = []
    # How many OTHER members of the system are fronting but are not named here
    # (fronting members not in this view). Never counts never-shareable or
    # fronting-private members - their front state does not propagate at all.
    # Always 0 when the view has fronting_show_count off.
    hidden_count: int = 0
