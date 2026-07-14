#!/usr/bin/env python3
"""Seed a demo account with realistic content for reviewers.

Originally just generated members + groups for UI scale-testing; expanded to
populate everything a Google Play reviewer (or anyone evaluating Sheaf for
the first time) would want to navigate: system profile, members with
markdown bios + pronouns + birthdays + privacy mix, groups, tags, custom
fields with values on a subset of members, ~30 days of front history, and
a handful of journals with edit history.

Since expanded again to cover the rest of the docs surface: notification
channels (all pointing at .invalid hosts, so nothing real gets traffic),
reminders, polls with votes cast, message-board posts and replies, typed
member relationships, a couple of archived members, generated member
avatars, one inactive server
announcement (needs an admin account; skipped with a log line otherwise),
and - opt-in - two extra small accounts so the admin Users table shows a
multi-user instance.

Usage:
    python3 scripts/seed_bulk_system.py

Environment overrides:
    SHEAF_URL        default http://localhost:8000
    SEED_EMAIL       default bulktest@demo.sheaf.sh
    SEED_PASSWORD    default testing-password-123
    SEED_EXTRA_USERS set to 1 to also seed demo2@/demo3@demo.sheaf.sh (same
                     password) with a few members each. Default off so the
                     base seed is unchanged for existing users.

If the target instance has REGISTRATION_MODE=approval or EMAIL_VERIFICATION=required,
approve/verify the account first:
    docker exec sheaf-db-1 psql -U sheaf -d sheaf -c \\
        "UPDATE users SET account_status='active', email_verified=true \\
         WHERE email_hash = (SELECT email_hash FROM users \\
                             WHERE account_status='pending_approval' \\
                             ORDER BY created_at DESC LIMIT 1);"
then re-run the script to populate content.
"""
import io
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


def upload_image(path, filename, png_bytes, token):
    """Multipart POST for the files API. `request` is JSON-only and this is
    the one endpoint that needs form-data, so it gets a tiny hand-rolled
    encoder instead of a requests dependency."""
    boundary = "sheafseedboundary"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode()
    data = head + png_bytes + f"\r\n--{boundary}--\r\n".encode()
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"ERROR POST {path}: {e.code} {body}", file=sys.stderr)
        raise


def generate_avatar_png(initial: str, color: str) -> bytes | None:
    """Render a small deterministic avatar: solid colour, white initial.

    Pillow is a server dependency, so it's always present under `uv run`.
    When the script is run with a bare python3 that doesn't have it, return
    None and the caller skips avatars rather than failing the whole seed.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    img = Image.new("RGB", (128, 128), color)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=72)
        draw.text((64, 64), initial, fill="#ffffff", font=font, anchor="mm")
    except (TypeError, ValueError):
        # Older Pillow: no sized default font / no anchor support. A small
        # off-centre initial still beats no avatar.
        draw.text((56, 56), initial, fill="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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

# The relationship-type presets the editor offers, minus parent/child (its
# implications get awkward fast in a demo system). Symmetric types omit a
# reverse label; directional / either types carry one.
RELATIONSHIP_TYPE_SPECS = [
    {"name": "Partner", "symmetry": "symmetric", "forward_label": "partner"},
    {"name": "Friend", "symmetry": "symmetric", "forward_label": "friend"},
    {"name": "Sibling", "symmetry": "symmetric", "forward_label": "sibling"},
    {
        "name": "Protector", "symmetry": "either",
        "forward_label": "protector", "reverse_label": "protectee",
    },
    {
        "name": "Caretaker", "symmetry": "either",
        "forward_label": "caretaker", "reverse_label": "cared for",
    },
    {
        "name": "Split", "symmetry": "directional",
        "forward_label": "split from", "reverse_label": "split off",
    },
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
# Notifications, reminders, polls, messages
# ---------------------------------------------------------------------------

# Every destination points at an RFC 2606 .invalid hostname: the channels
# look real in the UI but can never resolve, so nothing leaves the box.
CHANNEL_SPECS = [
    {
        "name": "Home automation hook",
        "destination_type": "webhook",
        "destination_config": {"url": "https://example.invalid/sheaf-hook"},
        "base_all_members": True,
    },
    {
        # Non-default trigger settings: fires on stop and cofront changes
        # too, redacts cofronters, minimal payloads, longer debounce, and
        # overnight quiet hours.
        "name": "Partner's ntfy",
        "destination_type": "ntfy",
        "destination_config": {
            "server_url": "https://ntfy.example.invalid",
            "topic": "sheaf-demo",
        },
        "base_all_members": True,
        "trigger_on_stop": True,
        "trigger_on_cofront_change": True,
        "cofront_redaction": "someone",
        "payload_sensitivity": "minimal",
        "debounce_seconds": 300,
        "quiet_hours": {"start": "23:00", "end": "08:00", "tz": "UTC"},
    },
    {
        # web_push channels sit in pending_registration until a recipient
        # redeems the activation link, so this one demos the pending state.
        "name": "Browser push (awaiting activation)",
        "destination_type": "web_push",
        "base_all_members": True,
    },
]

# channel_id is filled in at runtime (reminders ride a channel).
REMINDER_SPECS = [
    {
        "name": "Evening meds",
        "title": "Meds check",
        "body": "Whoever's fronting: evening meds are in the kitchen drawer.",
        "trigger_type": "repeated",
        "schedule_kind": "daily",
        "schedule_time": "21:30",
    },
    {
        # schedule_dow_mask: bit 0 = Monday .. bit 6 = Sunday, so 64 =
        # Sundays only.
        "name": "Sunday system meeting",
        "title": "System meeting in an hour",
        "body": "Weekly check-in: journal highlights, front plans, chores.",
        "trigger_type": "repeated",
        "schedule_kind": "weekly",
        "schedule_time": "19:00",
        "schedule_dow_mask": 64,
    },
    {
        "name": "Grounding after a switch",
        "title": "Take five",
        "body": "You've just fronted. Water, stretch, check the message board.",
        "trigger_type": "automated",
        "trigger_event": "start",
        "delay_seconds": 1800,
    },
]

POLL_SPECS = [
    {
        # Long-running poll with live results, so the tally renders.
        "poll": {
            "question": "Where should this year's holiday go?",
            "description": (
                "Same rules as last year: one vote each, grudges about "
                "the outcome expire after a week."
            ),
            "kind": "single_choice",
            "results_visibility": "live",
        },
        "options": ["Sea", "Forest cabin", "Stay home, day trips", "City + museums"],
        "closes_in_minutes": 7 * 24 * 60,
        "voters": 9,
    },
    {
        # The API enforces a minimum close window (an hour by default), so
        # an already-closed poll can't be seeded over HTTP. This one closes
        # just past the floor: an hour after seeding, the polls screen has
        # a closed poll whose end_only results have just been revealed.
        "poll": {
            "question": "Rename the group chat?",
            "description": None,
            "kind": "multi_choice",
            "results_visibility": "end_only",
        },
        "options": ["Keep it", "The Grove", "Root Directory", "Tree(s) of Trust"],
        "closes_in_minutes": 65,
        "voters": 6,
    },
]

# (author, body) posts on the system board, in order.
MESSAGE_BOARD_POSTS = [
    ("Oak", "Dentist rescheduled to Thursday 14:00. Whoever's fronting, "
        "please actually go this time."),
    ("Fir", "The leftover curry in the blue container is claimed. By me. "
        "This is not a negotiation."),
    ("Birch", "Three library books due Saturday - they're on the hall table."),
    ("Dahlia", "New sketchbook lives on the top shelf. Clean hands, please."),
    ("Ash", "Reminder that the neighbours' cat is NOT ours and does not "
        "need feeding twice."),
]

# (author, index into MESSAGE_BOARD_POSTS to reply to, body)
MESSAGE_REPLIES = [
    ("Beech", 0, "Noted - added to the calendar and set a reminder."),
    ("Elm", 4, "It looked hungry. I regret nothing."),
]

# (wall owner, author, body) posts on member walls.
MEMBER_WALL_POSTS = [
    ("Alder", "Elm", "Thanks for taking that phone call yesterday. "
        "You didn't have to, and it helped."),
    ("Alder", "Beech", "Your umbrella is by the door. You always ask."),
]

# Quiet late-alphabet members nothing else references prominently, so the
# archived-members view has content without changing the familiar screens.
ARCHIVED_MEMBER_NAMES = ["Ulmus", "Zelkova"]

# The members that feature most in screenshots get avatars.
AVATAR_MEMBER_NAMES = [
    "Alder", "Ash", "Beech", "Birch", "Fir", "Oak", "Dahlia", "Elm",
]

# Inactive on purpose: it shows up in the admin announcements list without
# stamping a banner across every page of the demo.
INACTIVE_ANNOUNCEMENT = {
    "title": "Scheduled maintenance (example)",
    "body": (
        "An example of an inactive announcement - visible in the admin "
        "list, never rendered as a banner."
    ),
    "severity": "info",
    "dismissible": True,
    "active": False,
}

# Small side accounts for the admin Users table (opt-in via
# SEED_EXTRA_USERS=1). Members are (name, pronouns, color).
EXTRA_ACCOUNTS = [
    {
        "email": "demo2@demo.sheaf.sh",
        "profile": {
            "name": "The Hearthside",
            "tag": "HRTH",
            "color": "#f97316",
            "privacy": "private",
        },
        "members": [
            ("Ember", "she/her", "#f97316"),
            ("Flint", "he/him", "#94a3b8"),
            ("Wick", "they/them", "#f59e0b"),
        ],
    },
    {
        "email": "demo3@demo.sheaf.sh",
        "profile": {
            "name": "Brook & Stone",
            "tag": "B&S",
            "color": "#3b82f6",
            "privacy": "private",
        },
        "members": [
            ("Brook", "they/them", "#3b82f6"),
            ("Stone", "he/they", "#94a3b8"),
        ],
    },
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
# Extra accounts
# ---------------------------------------------------------------------------


def seed_extra_account(account: dict):
    """Register (or log back into) one of the small side accounts and give
    it just enough content to look real in the admin Users table: a system
    profile, a few members, and someone at front."""
    email = account["email"]
    try:
        auth = request(
            "POST",
            "/v1/auth/register",
            {"email": email, "password": PASSWORD, "newsletter_opt_in": False},
        )
        print(f"Registered extra account {email}")
    except urllib.error.HTTPError:
        auth = request(
            "POST",
            "/v1/auth/login",
            {"email": email, "password": PASSWORD},
        )
        print(f"Logged in existing extra account {email}")
    extra_token = auth["access_token"]

    request("PATCH", "/v1/systems/me", account["profile"], extra_token)
    ids: list[str] = []
    for name, pronouns, color in account["members"]:
        m = request(
            "POST",
            "/v1/members",
            {"name": name, "pronouns": pronouns, "color": color, "privacy": "private"},
            extra_token,
        )
        ids.append(m["id"])
    request(
        "POST",
        "/v1/fronts",
        {
            "member_ids": [ids[0]],
            "started_at": iso(datetime.now(UTC) - timedelta(hours=3)),
            "replace_fronts": True,
        },
        extra_token,
    )
    print(f"  {account['profile']['name']}: {len(ids)} members, 1 open front")


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
    system = request("PATCH", "/v1/systems/me", SYSTEM_PROFILE, token)
    print(f"System profile set: {SYSTEM_PROFILE['name']}")

    # Members
    member_ids: list[str] = []
    member_id_by_name: dict[str, str] = {}
    member_color_by_name: dict[str, str] = {}
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
        member_id_by_name[name] = m["id"]
        member_color_by_name[name] = m["color"] or "#7c3aed"
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

    # Relationships. Define the preset types, then draw a web of edges between
    # random member pairs. `either` types are made mutual ~40% of the time; the
    # rest (and directional) are left one-directional. Dedup on the unordered
    # (type, pair) so we never hit the server's uniqueness 409.
    # Type names are unique per system, so reuse any that already exist (from a
    # prior run or manual testing) rather than 409ing and aborting.
    existing_types = {
        t["name"]: t["id"]
        for t in request("GET", "/v1/relationship-types", None, token)
    }
    rel_type_ids: dict[str, str] = {}
    for spec in RELATIONSHIP_TYPE_SPECS:
        if spec["name"] in existing_types:
            rel_type_ids[spec["name"]] = existing_types[spec["name"]]
        else:
            rt = request("POST", "/v1/relationship-types", spec, token)
            rel_type_ids[spec["name"]] = rt["id"]
    print(f"Ensured {len(rel_type_ids)} relationship types")

    seen_rel: set[tuple[str, frozenset]] = set()
    target_rels = len(member_ids)
    made_rels = 0
    attempts = 0
    while made_rels < target_rels and attempts < target_rels * 4:
        attempts += 1
        a, b = random.sample(member_ids, 2)
        spec = random.choice(RELATIONSHIP_TYPE_SPECS)
        type_id = rel_type_ids[spec["name"]]
        key = (type_id, frozenset({a, b}))
        if key in seen_rel:
            continue
        seen_rel.add(key)
        body = {"source_id": a, "target_id": b, "relationship_type_id": type_id}
        if spec["symmetry"] == "either" and random.random() < 0.4:
            body["mutual"] = True
        request("POST", "/v1/member-relationships", body, token)
        made_rels += 1
    print(f"Created {made_rels} member relationships")

    # Front history. A previous run leaves its newest front open; close it
    # first, or replaying history from 30 days back would try to end it
    # before it started (the API rightly refuses). No-op on a fresh DB.
    for open_front in request("GET", "/v1/fronts/current", None, token):
        request(
            "PATCH",
            f"/v1/fronts/{open_front['id']}",
            {"ended_at": iso(datetime.now(UTC))},
            token,
        )
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

    # Notification channels. Channels hang off a watch token (the share
    # grant), so create one token standing in for a trusted person first.
    wt = request(
        "POST",
        f"/v1/systems/{system['id']}/watch-tokens",
        {"label": "Shared with partner"},
        token,
    )
    channel_ids: dict[str, str] = {}
    for spec in CHANNEL_SPECS:
        created = request(
            "POST", f"/v1/watch-tokens/{wt['id']}/channels", spec, token
        )
        channel_ids[spec["name"]] = created["channel"]["id"]
    print(f"Created watch token + {len(CHANNEL_SPECS)} notification channels")

    # Reminders ride a channel; the ntfy one points at a .invalid host, so
    # any real firing goes nowhere.
    reminder_channel = channel_ids["Partner's ntfy"]
    for spec in REMINDER_SPECS:
        request(
            "POST",
            "/v1/reminders",
            {**spec, "channel_id": reminder_channel},
            token,
        )
    print(f"Created {len(REMINDER_SPECS)} reminders")

    # Polls, with votes cast while they're open.
    for spec in POLL_SPECS:
        closes_at = datetime.now(UTC) + timedelta(minutes=spec["closes_in_minutes"])
        poll = request(
            "POST",
            "/v1/polls",
            {
                **spec["poll"],
                "closes_at": iso(closes_at),
                "options": [{"text": text} for text in spec["options"]],
            },
            token,
        )
        option_ids = [o["id"] for o in poll["options"]]
        voters = random.sample(member_ids, spec["voters"])
        for mid in voters:
            picks = (
                random.sample(option_ids, random.randint(1, 2))
                if spec["poll"]["kind"] == "multi_choice"
                else [random.choice(option_ids)]
            )
            request(
                "POST",
                f"/v1/polls/{poll['id']}/votes",
                {"voted_as_member_id": mid, "option_ids": picks},
                token,
            )
        print(f"Poll '{spec['poll']['question']}' with {len(voters)} votes")

    # Message boards: a system-board conversation with replies, plus a
    # couple of posts on a member's wall.
    board_msg_ids: list[str] = []
    for author, text in MESSAGE_BOARD_POSTS:
        msg = request(
            "POST",
            "/v1/messages",
            {
                "board_kind": "system",
                "author_member_id": member_id_by_name[author],
                "body": text,
            },
            token,
        )
        board_msg_ids.append(msg["id"])
    for author, parent_index, text in MESSAGE_REPLIES:
        request(
            "POST",
            "/v1/messages",
            {
                "board_kind": "system",
                "author_member_id": member_id_by_name[author],
                "parent_message_id": board_msg_ids[parent_index],
                "body": text,
            },
            token,
        )
    for wall_owner, author, text in MEMBER_WALL_POSTS:
        request(
            "POST",
            "/v1/messages",
            {
                "board_kind": "member",
                "board_member_id": member_id_by_name[wall_owner],
                "author_member_id": member_id_by_name[author],
                "body": text,
            },
            token,
        )
    print(
        f"Posted {len(MESSAGE_BOARD_POSTS)} board messages, "
        f"{len(MESSAGE_REPLIES)} replies, {len(MEMBER_WALL_POSTS)} wall posts"
    )

    # Archive a couple of quiet members so the archived view has content.
    # Archiving is a reversible soft-hide; their front history stays put.
    for name in ARCHIVED_MEMBER_NAMES:
        request(
            "POST",
            f"/v1/members/{member_id_by_name[name]}/archive",
            None,
            token,
        )
    print(f"Archived {len(ARCHIVED_MEMBER_NAMES)} members")

    # Avatars for the members that feature most. Generated locally (solid
    # colour + initial), so nothing external is fetched. Uploads can be
    # disabled instance-wide or per-account; skip the lot if so.
    uploaded = 0
    for name in AVATAR_MEMBER_NAMES:
        png = generate_avatar_png(name[0], member_color_by_name[name])
        if png is None:
            print(
                "Skipped avatars: Pillow not available "
                "(run via `uv run` to include it)"
            )
            break
        try:
            up = upload_image(
                "/v1/files/upload?purpose=avatar",
                f"{name.lower()}.png",
                png,
                token,
            )
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print(
                    "Skipped avatars: image uploads are disabled for "
                    "this account"
                )
                break
            raise
        request(
            "PATCH",
            f"/v1/members/{member_id_by_name[name]}",
            {"avatar_url": up["key"]},
            token,
        )
        uploaded += 1
    if uploaded:
        print(f"Uploaded avatars for {uploaded} members")

    # One inactive server announcement so the admin announcements screen
    # isn't empty. Needs an admin account; regular seeds get a 403 here,
    # which is fine - skip and say so.
    try:
        request("POST", "/v1/admin/announcements", INACTIVE_ANNOUNCEMENT, token)
        print(f"Created inactive announcement '{INACTIVE_ANNOUNCEMENT['title']}'")
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print("Skipped announcement: account is not a server admin")
        else:
            raise

    # Extra small accounts so the admin Users table looks like a real
    # multi-user instance. Off by default: existing users of this script
    # get exactly the same single-account seed as before.
    if os.environ.get("SEED_EXTRA_USERS") == "1":
        for account in EXTRA_ACCOUNTS:
            seed_extra_account(account)

    print()
    print(f"Login: {EMAIL} / {PASSWORD}")
    print(f"  at: {BASE}")


if __name__ == "__main__":
    main()
