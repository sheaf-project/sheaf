#!/usr/bin/env python3
"""Seed a demo account with realistic content for reviewers.

Originally just generated members + groups for UI scale-testing; expanded to
populate everything a Google Play reviewer (or anyone evaluating Sheaf for
the first time) would want to navigate: system profile, members with
markdown bios + pronouns + birthdays + privacy mix, groups, tags, custom
fields with values on a subset of members, ~30 days of front history, and
a handful of journals with edit history.

Usage:
    python3 scripts/seed_bulk_system.py

Environment overrides:
    SHEAF_URL        default http://localhost:8000
    SEED_EMAIL       default bulktest@demo.sheaf.sh
    SEED_PASSWORD    default testing-password-123

If the target instance has REGISTRATION_MODE=approval or EMAIL_VERIFICATION=required,
approve/verify the account first:
    docker exec sheaf-db-1 psql -U sheaf -d sheaf -c \\
        "UPDATE users SET account_status='active', email_verified=true \\
         WHERE email_hash = (SELECT email_hash FROM users \\
                             WHERE account_status='pending_approval' \\
                             ORDER BY created_at DESC LIMIT 1);"
then re-run the script to populate content.
"""
import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

BASE = os.environ.get("SHEAF_URL", "http://localhost:8000")
EMAIL = os.environ.get("SEED_EMAIL", "bulktest@demo.sheaf.sh")
PASSWORD = os.environ.get("SEED_PASSWORD", "testing-password-123")

# Deterministic-ish output so re-runs against a fresh DB produce the same
# story. Reviewers see consistent state across resets.
random.seed(0xDEAD)


def request(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"ERROR {method} {path}: {e.code} {body}", file=sys.stderr)
        raise


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Content templates
# ---------------------------------------------------------------------------

NAMES = [
    "Alder", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Ginkgo", "Hazel",
    "Iris", "Juniper", "Koa", "Linden", "Maple", "Nyssa", "Oak", "Pine",
    "Quince", "Rowan", "Sumac", "Thuja", "Ulmus", "Vine", "Willow", "Xylia",
    "Yew", "Zelkova", "Ash", "Beech", "Cypress", "Dahlia", "Ebony", "Fern",
    "Holly", "Ivy", "Jade", "Kudzu", "Mint", "Nettle",
    "Orchid", "Poppy", "Quill", "Reed", "Sage", "Tansy",
]

PRONOUNS_POOL = [
    "she/her", "he/him", "they/them", "she/they", "he/they",
    "they/she", "they/he", "xe/xem", "it/its", None, None, None,
]

BIO_TEMPLATES = [
    "Bookish, drinks too much tea. Likes maps and well-organised spice racks.",
    "Loud one. Will pick a fight with a vending machine and lose with style.",
    "The careful one. Reads instructions, double-checks doors, "
    "remembers everyone's birthdays.",
    "**Plant nerd.** Knows every leaf in a five-mile radius by name.\n\n"
    "Currently obsessed with: ferns, terrariums, the smell of soil.",
    "Gentle. Soft voice, soft hands, *very* sharp opinions about pasta.",
    "Loves a long walk. Will tell you about birds whether you asked or not.",
    "Night owl. Best ideas arrive between 1 and 4 AM, in *exactly that order*.",
    "The cook. Will feed you whether you're hungry or not.\n\n"
    "Specialty: anything braised, slowly, on a Sunday.",
    "Quiet, but the kind of quiet that's listening.",
    "Energetic morning person. Sorry.",
    "Studious. Three half-finished journals on the bedside table at any time.",
    "The protective one. Watches the door, keeps the keys, "
    "remembers the locksmith's number.",
    "Whimsical. Believes in the personhood of houseplants.",
    "Fixer. If something's broken in the house, this one's already on it.",
]

BIRTHDAY_FORMATS = ["1991-03-14", "1988-11-22", "1995-07-04", None, None, None]
PRIVACY_LEVELS = [
    "public", "private", "private", "private",  # bias toward private
    "friends",
]

GROUP_SPECS = [
    ("Core", "#7c3aed", "Members who front regularly.", 12),
    ("Introjects", "#10b981", "Members based on outside sources.", 8),
    ("Protectors", "#ef4444", "Members who front during stress / "
        "emotional intensity.", 6),
    ("Guests", "#f59e0b", "Members who front rarely or in unusual "
        "circumstances.", 5),
    ("Archivists", "#3b82f6", "Members who keep our memory and history.", 7),
]

TAG_SPECS = [
    ("primary-fronter", "#7c3aed"),
    ("rare-fronter", "#94a3b8"),
    ("co-conscious", "#10b981"),
    ("trauma-holder", "#ef4444"),
    ("creative", "#ec4899"),
    ("logical", "#3b82f6"),
]

CUSTOM_FIELDS = [
    ("Role", "text", None),
    ("Age", "text", None),
    ("Favourite food", "text", None),
    (
        "Comfort level",
        "select",
        {"choices": ["high", "medium", "low", "varies"]},
    ),
    ("Out to family?", "boolean", None),
]

ROLE_VALUES = [
    "host", "co-host", "protector", "caretaker", "child", "scholar",
    "creative", "fixer", "watcher", "trickster", "guide",
]
AGE_VALUES = ["~12", "~16", "~20", "~25", "~30", "~35", "~50", "ageless"]
FOOD_VALUES = [
    "ramen", "sourdough", "miso soup", "roasted vegetables", "pho",
    "chocolate", "fresh bread", "iced coffee", "tea + biscuits",
]


SYSTEM_PROFILE = {
    "name": "The Sheaf Demo",
    "description": (
        "Demo account for evaluating Sheaf. Populated with synthetic "
        "members, fronts, and journals. Nothing here is real personal "
        "data — fronters are tree species and bios are templated."
    ),
    "tag": "DEMO",
    "color": "#7c3aed",
    "privacy": "private",
    "date_format": "ymd",
    "replace_fronts_default": True,
}


JOURNAL_ENTRIES = [
    {
        "title": "Welcome to the demo",
        "body": (
            "## Welcome\n\n"
            "This is the demo account. Browse around — we've populated "
            "members, groups, fronts, and journals with synthetic data so "
            "every screen has something to look at.\n\n"
            "Try:\n\n"
            "- The **Members** list with privacy filters\n"
            "- The **Fronts** screen — there's about a month of switch history\n"
            "- The **Journals** tab on individual members\n"
            "- **Settings → System Safety** — see the destructive-action "
            "grace period in action\n"
        ),
        "edits": [
            "## Welcome\n\nThis is the demo account.",
            "## Welcome\n\nThis is the demo account. Browse around.",
        ],
    },
    {
        "title": "Notes on cofronting",
        "body": (
            "Two of us today, and the world's a bit louder than usual.\n\n"
            "It's like having a stereo on — neither voice is wrong, they "
            "just both want to talk."
        ),
    },
    {
        "title": "Sleep cycle finally syncing",
        "body": (
            "Three weeks of going to bed at a reasonable hour and we're "
            "finally feeling it. Mornings are no longer the enemy."
        ),
    },
]


PER_MEMBER_JOURNAL_TEMPLATES = [
    "Quiet day. Nothing in particular to report — just here.",
    "Worked on the garden. The mint is winning the battle for the box "
    "but we're letting it.",
    "Long walk this morning. Saw a heron. Took it as a sign.",
    "Reading that book the others started last week. Not sure I'm a fan "
    "of the protagonist.",
    "Finally finished the bookshelf. Photo for the journal would be ideal "
    "but I forgot to take one.",
]


# ---------------------------------------------------------------------------
# Front history generation
# ---------------------------------------------------------------------------


def generate_front_history(member_ids: list[str], days: int = 30):
    """Produce a list of (started_at, member_ids) tuples, in chronological
    order, for `days` days ending at "now". Mix of solo and cofront.

    The most recent entry is left open (currently fronting) so the front
    screen has a meaningful "now" state.
    """
    events: list[tuple[datetime, list[str]]] = []
    cursor = datetime.now(UTC) - timedelta(days=days)

    while cursor < datetime.now(UTC) - timedelta(minutes=20):
        # Switch every 1.5–10 hours
        gap_hours = random.uniform(1.5, 10)
        cursor += timedelta(hours=gap_hours)

        # 65% solo, 30% cofront-of-2, 5% cofront-of-3
        roll = random.random()
        if roll < 0.65:
            count = 1
        elif roll < 0.95:
            count = 2
        else:
            count = 3
        members = random.sample(member_ids, count)
        events.append((cursor, members))

    return events


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    try:
        auth = request(
            "POST",
            "/v1/auth/register",
            {"email": EMAIL, "password": PASSWORD, "newsletter_opt_in": False},
        )
        print(f"Registered {EMAIL}")
    except urllib.error.HTTPError:
        auth = request(
            "POST",
            "/v1/auth/login",
            {"email": EMAIL, "password": PASSWORD},
        )
        print(f"Logged in existing {EMAIL}")

    token = auth["access_token"]

    # System profile
    request("PATCH", "/v1/systems/me", SYSTEM_PROFILE, token)
    print(f"System profile set: {SYSTEM_PROFILE['name']}")

    # Members
    member_ids: list[str] = []
    for i, name in enumerate(NAMES):
        m = request(
            "POST",
            "/v1/members",
            {
                "name": name,
                "description": random.choice(BIO_TEMPLATES),
                "pronouns": random.choice(PRONOUNS_POOL),
                "color": random.choice(
                    [
                        "#7c3aed", "#10b981", "#f59e0b", "#ef4444",
                        "#3b82f6", "#ec4899", "#14b8a6", "#f97316",
                    ]
                ),
                "birthday": random.choice(BIRTHDAY_FORMATS),
                "privacy": random.choice(PRIVACY_LEVELS),
            },
            token,
        )
        member_ids.append(m["id"])
        if (i + 1) % 10 == 0:
            print(f"  ...{i + 1}/{len(NAMES)} members")
    print(f"Created {len(member_ids)} members")

    # Groups
    for gname, gcolor, gdesc, count in GROUP_SPECS:
        g = request(
            "POST",
            "/v1/groups",
            {"name": gname, "color": gcolor, "description": gdesc},
            token,
        )
        chosen = random.sample(member_ids, count)
        request(
            "PUT",
            f"/v1/groups/{g['id']}/members",
            {"member_ids": chosen},
            token,
        )
        print(f"Group '{gname}' with {count} members")

    # Tags + memberships
    tag_ids: list[str] = []
    for tname, tcolor in TAG_SPECS:
        t = request(
            "POST",
            "/v1/tags",
            {"name": tname, "color": tcolor},
            token,
        )
        tag_ids.append(t["id"])
    # Scatter ~30-50% of members into each tag, with overlap so tags
    # behave like real ad-hoc labels rather than partitions.
    for tag_id in tag_ids:
        share = random.randint(len(member_ids) // 4, len(member_ids) // 2)
        chosen = random.sample(member_ids, share)
        request(
            "PUT",
            f"/v1/tags/{tag_id}/members",
            {"member_ids": chosen},
            token,
        )
    print(f"Created {len(TAG_SPECS)} tags with member assignments")

    # Custom fields + values
    field_ids: dict[str, str] = {}
    for order, (fname, ftype, foptions) in enumerate(CUSTOM_FIELDS):
        body = {
            "name": fname,
            "field_type": ftype,
            "order": order,
            "privacy": "private",
        }
        if foptions:
            body["options"] = foptions
        f = request("POST", "/v1/fields", body, token)
        field_ids[fname] = f["id"]
    print(f"Created {len(field_ids)} custom fields")

    # Set values on a random ~half of members
    populated = random.sample(member_ids, len(member_ids) // 2)
    for mid in populated:
        values = [
            {"field_id": field_ids["Role"], "value": random.choice(ROLE_VALUES)},
            {"field_id": field_ids["Age"], "value": random.choice(AGE_VALUES)},
            {
                "field_id": field_ids["Favourite food"],
                "value": random.choice(FOOD_VALUES),
            },
            {
                "field_id": field_ids["Comfort level"],
                "value": random.choice(["high", "medium", "low", "varies"]),
            },
            {
                "field_id": field_ids["Out to family?"],
                "value": random.choice([True, False]),
            },
        ]
        request("PUT", f"/v1/members/{mid}/fields", values, token)
    print(f"Populated custom-field values on {len(populated)} members")

    # Front history
    events = generate_front_history(member_ids, days=30)
    for started_at, mids in events:
        request(
            "POST",
            "/v1/fronts",
            {
                "member_ids": mids,
                "started_at": iso(started_at),
                "replace_fronts": True,
            },
            token,
        )
    print(
        f"Created {len(events)} front events over the last 30 days "
        f"(latest is currently open)"
    )

    # Journals — system-wide first
    for entry in JOURNAL_ENTRIES:
        j = request(
            "POST",
            "/v1/journals",
            {"title": entry["title"], "body": entry["body"]},
            token,
        )
        # Replay edits to build revision history. The first edit creates
        # the auto-pinned original, subsequent ones accumulate.
        for older_body in entry.get("edits", []):
            request(
                "PATCH",
                f"/v1/journals/{j['id']}",
                {"body": older_body},
                token,
            )
            # Edit it back to the canonical version so the "current" view
            # matches `body` and the revisions tail captures the journey.
            request(
                "PATCH",
                f"/v1/journals/{j['id']}",
                {"body": entry["body"]},
                token,
            )
    print(f"Created {len(JOURNAL_ENTRIES)} system-wide journal entries")

    # A few per-member journals on randomly chosen members
    journaling_members = random.sample(member_ids, 6)
    for mid in journaling_members:
        request(
            "POST",
            "/v1/journals",
            {
                "member_id": mid,
                "title": None,
                "body": random.choice(PER_MEMBER_JOURNAL_TEMPLATES),
            },
            token,
        )
    print(f"Created per-member journals on {len(journaling_members)} members")

    print()
    print(f"Login: {EMAIL} / {PASSWORD}")
    print(f"  at: {BASE}")


if __name__ == "__main__":
    main()
