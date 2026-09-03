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

Every payload that names a member carries exactly ONE name field, `name`,
holding what a visitor actually reads (the display name, falling back to the
decrypted name). There is deliberately no `display_name` beside it anywhere:
setting a display name is a request not to be called the other thing, and
publishing the other thing one key along would have made that request
cosmetic - a scraper reads JSON, not the rendered page.
"""

from pydantic import BaseModel


class PublicMemberField(BaseModel):
    # One custom-field entry on a member card: the definition's name and this
    # member's value for it. A LIST of these rather than a name-keyed map on
    # purpose - field names are not unique, so two definitions a view exposes
    # that happen to share a name must BOTH reach the card. Keying by name
    # silently dropped the first of any such pair.
    name: str
    value: object


class PublicMemberView(BaseModel):
    # Identity always shown for a member the owner put in a view.
    id: str
    # The shown name. See the module docstring: one name field, never two.
    name: str
    pronouns: str | None = None
    avatar_url: str | None = None
    banner_url: str | None = None
    color: str | None = None
    # Only populated when the view has include_bio on. Markdown, with embedded
    # image refs resolved/signed like any other rendered bio.
    bio: str | None = None
    # One entry per custom field this view exposes that this member has a value
    # for, in selection order. Empty when the view exposes no fields. A list, not
    # a map: field names are not unique, so same-named fields must both appear.
    fields: list[PublicMemberField] = []


class PublicSystemView(BaseModel):
    # The system's own id, and ONLY on the routes that are already addressed by
    # it (`/public/systems/{system_id}/...`). Null on a share link: the link is
    # an opaque token precisely so the system it belongs to is not learnable
    # from it, and an id sitting in the body would have let two links - or a
    # link and a public profile - be correlated back to one system by anything
    # reading the JSON. See `share_projection.project_system`.
    id: str | None = None
    name: str
    description: str | None = None
    avatar_url: str | None = None
    color: str | None = None
    tag: str | None = None
    # Count of members actually visible in this view (after the hard guards),
    # not the system's real member count. Deliberately no created_at: the age
    # of a system is mildly sensitive and worthless to a visitor.
    #
    # NULL when the view does not include the member roster at all. A roster
    # the view refuses to serve must not be countable either: "23 members you
    # cannot see" is still a fact about the system, and it is exactly the fact
    # someone turning the roster off was trying not to publish.
    member_count: int | None = None
    # Whether this view hands out per-member addresses. Presentation
    # configuration, not a secret: the client needs it to decide whether a
    # member card is a link to a page of its own or opens in place, and a
    # visitor could learn the same thing by clicking. It discloses nothing the
    # /members/{id} route does not already answer.
    member_permalinks: bool = False


class PublicFrontingMember(BaseModel):
    """A lite identity card for a currently-fronting member.

    Deliberately NOT a PublicMemberView: the "who is fronting right now" surface
    is the sharpest one (real-time presence, polled repeatedly), so it carries
    only identity + when the front started. Bio and custom fields, even when the
    view exposes them, are reached through the /members endpoint, not handed out
    on every fronting poll.
    """

    id: str
    # The shown name, exactly as on `PublicMemberView`.
    name: str
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


class PublicRelationshipEndpoint(BaseModel):
    """One end of a published edge.

    Just an id and a name, on purpose. The endpoint is always a member the same
    view already publishes in full through /members, so repeating avatars and
    pronouns here would only add a second place for the member payload to drift
    - the client joins on `id` for anything richer.
    """

    id: str
    name: str


class PublicRelationship(BaseModel):
    """One edge between two members this view publishes.

    Reaching this schema at all means three separate gates were passed: the
    view has include_relationships on, the edge itself is marked `public`, and
    BOTH endpoints cleared the member ceiling. Nothing about the relationship
    TYPE is sensitive on its own, so the type's name and colour ride along; the
    private thing is which two people it joins, and that is what the gates
    protect.
    """

    id: str
    type_name: str
    type_color: str | None = None
    source: PublicRelationshipEndpoint
    target: PublicRelationshipEndpoint
    # How the edge reads from each end ("parent" / "child"). Both are
    # forward_label for symmetric types and for mutual either-edges.
    source_label: str
    target_label: str
    # True only for an `either` type edge the owner marked mutual. The client
    # uses it, with the two labels, to decide whether to draw an arrow.
    mutual: bool


class PublicRelationshipsView(BaseModel):
    relationships: list[PublicRelationship] = []


class PublicGroupMember(BaseModel):
    """One member of a published group.

    Id and name only, exactly like `PublicRelationshipEndpoint` and for the
    same reason: everyone listed here is already published in full through
    /members, so repeating the member payload would only give it a second
    place to drift. The client joins on `id` for anything richer.
    """

    id: str
    name: str


class PublicGroupView(BaseModel):
    """One group this view publishes.

    Reaching this schema means two gates were passed: the view has
    include_groups on, and the group itself is marked `public`. The roster is
    not a third gate but an INTERSECTION - it lists only members this view was
    already showing, so publishing a group can never name somebody new.

    A public group whose intersection is EMPTY does not reach this schema at
    all: a bare name and description with nobody behind them still tells a
    visitor that such a group exists in this system, which is not something the
    owner published by publishing a roster. See
    `share_projection._projectable_group_rosters`.
    """

    id: str
    name: str
    description: str | None = None
    color: str | None = None
    members: list[PublicGroupMember] = []


class PublicGroupsView(BaseModel):
    groups: list[PublicGroupView] = []
