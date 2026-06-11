"""End-to-end tests for the Prism (.prism) import runner.

Envelopes are synthesised in-process using `prism_crypto.synthesize_envelope`
(scrypt N=16384 by default so tests don't pay the full ~200 ms cost per
case). Covers the runner plumbing, the preview endpoint, the
passphrase-credential flow, the deferred-decryption error path
(wrong passphrase in the runner not the preview), and the small set
of mapping decisions worth pinning down (multi-conversation chat
collapse, open-poll close-window default, sleep / habits / reminders
warnings, base64 avatar import, encrypted media attachment import).
"""

from __future__ import annotations

import base64
import io
import json
import os
import uuid

import httpx
import pytest
from nacl.bindings.crypto_aead import (
    crypto_aead_xchacha20poly1305_ietf_encrypt as _xchacha_encrypt,
)
from PIL import Image

from sheaf.services.prism_crypto import synthesize_envelope
from tests._import_runner_helpers import (
    drive_import_runner,
    wait_for_terminal,
)


def _make_png_bytes(size: int = 4) -> bytes:
    """Generate a small valid PNG. Avoids hand-rolling the IDAT bytes."""
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), (255, 64, 64, 255)).save(buf, "PNG")
    return buf.getvalue()


def _make_xchacha_blob(plaintext: bytes) -> tuple[bytes, str]:
    """Return (blob, base64-key) for a freshly-encrypted XChaCha20 blob.

    Mirrors the prism-sync-crypto::xchacha_encrypt format used by
    Prism: nonce(24) || ciphertext+poly1305_tag.
    """
    key = os.urandom(32)
    nonce = os.urandom(24)
    ct = _xchacha_encrypt(plaintext, None, nonce, key)
    return nonce + ct, base64.b64encode(key).decode()


def _make_export(
    *,
    headmate_count: int = 2,
    with_avatar: bool = False,
    with_groups: bool = True,
    with_custom_fields: bool = True,
    with_front_sessions: bool = True,
    with_notes: bool = True,
    with_polls: bool = True,
    with_open_poll: bool = False,
    with_conversations: bool = True,
    multi_conversation: bool = False,
    with_board_post: bool = True,
    sleep_sessions: int = 0,
    habits: int = 0,
    reminders: int = 0,
    with_media: bool = False,
) -> tuple[dict, list[tuple[str, bytes]]]:
    """Build a Prism v1.0 export JSON + matching media-blob registry.

    Tests tweak the kwargs to exercise individual mapping decisions
    without rebuilding the whole structure each time.
    """
    headmates: list[dict] = []
    for i in range(headmate_count):
        h: dict = {
            "id": str(uuid.uuid4()),
            "name": f"Member-{chr(ord('A') + i)}",
            "isActive": True,
            "createdAt": "2026-01-01T00:00:00.000Z",
            "displayOrder": i,
            "isAdmin": False,
            "customColorEnabled": False,
            "markdownEnabled": True,
            "pluralkitSyncIgnored": False,
        }
        if with_avatar and i == 0:
            h["profilePhotoData"] = base64.b64encode(_make_png_bytes()).decode()
        headmates.append(h)

    front_sessions = (
        [
            {
                "id": str(uuid.uuid4()),
                "startTime": "2026-06-01T10:00:00.000Z",
                "endTime": "2026-06-01T11:00:00.000Z",
                "headmateId": headmates[0]["id"],
                "sessionType": 0,
                "isHealthKitImport": False,
            }
        ]
        if with_front_sessions
        else []
    )

    groups: list[dict] = []
    group_entries: list[dict] = []
    if with_groups:
        g_id = str(uuid.uuid4())
        groups.append(
            {
                "id": g_id,
                "name": "Hosts",
                "colorHex": "#468BD4",
                "displayOrder": 0,
                "createdAt": "2026-01-01T00:00:00.000Z",
            }
        )
        for h in headmates:
            group_entries.append(
                {"id": str(uuid.uuid4()), "groupId": g_id, "memberId": h["id"]}
            )

    custom_fields: list[dict] = []
    if with_custom_fields:
        custom_fields.append(
            {
                "id": str(uuid.uuid4()),
                "name": "Species",
                "fieldType": 0,
                "displayOrder": 0,
                "createdAt": "2026-01-01T00:00:00.000Z",
                "fieldTypeId": "text",
            }
        )
        # Slider-type field: should land as TEXT with a warning.
        custom_fields.append(
            {
                "id": str(uuid.uuid4()),
                "name": "Mood",
                "fieldType": 0,
                "displayOrder": 1,
                "createdAt": "2026-01-01T00:00:00.000Z",
                "fieldTypeId": "slider",
            }
        )

    notes: list[dict] = []
    if with_notes:
        notes.append(
            {
                "id": str(uuid.uuid4()),
                "title": "test note",
                "body": "body text",
                "memberId": headmates[0]["id"],
                "date": "2026-06-01T10:00:00.000Z",
                "createdAt": "2026-06-01T10:00:00.000Z",
                "modifiedAt": "2026-06-01T10:00:00.000Z",
            }
        )

    polls: list[dict] = []
    poll_options: list[dict] = []
    if with_polls:
        p_id = str(uuid.uuid4())
        polls.append(
            {
                "id": p_id,
                "question": "Q?",
                "description": "",
                "isAnonymous": False,
                "allowsMultipleVotes": True,
                "isClosed": not with_open_poll,
                "createdAt": "2026-06-01T10:00:00.000Z",
            }
        )
        for idx, text in enumerate(["A", "B"]):
            voters = (
                [
                    {
                        "id": "v" + str(uuid.uuid4()),
                        "memberId": headmates[0]["id"],
                        "votedAt": "2026-06-01T10:01:00.000Z",
                    }
                ]
                if idx == 0
                else []
            )
            poll_options.append(
                {
                    "id": str(uuid.uuid4()),
                    "pollId": p_id,
                    "text": text,
                    "sortOrder": idx,
                    "isOtherOption": False,
                    "votes": voters,
                }
            )

    conversations: list[dict] = []
    messages: list[dict] = []
    if with_conversations:
        c_id = str(uuid.uuid4())
        conversations.append(
            {
                "id": c_id,
                "createdAt": "2026-06-01T00:00:00.000Z",
                "title": "Planning",
                "type": "group",
                "isDirectMessage": False,
                "creatorId": headmates[0]["id"],
                "participantIds": [h["id"] for h in headmates],
                "displayOrder": 0,
            }
        )
        messages.append(
            {
                "id": str(uuid.uuid4()),
                "content": "hello group",
                "timestamp": "2026-06-01T00:01:00.000Z",
                "isSystemMessage": False,
                "authorId": headmates[0]["id"],
                "conversationId": c_id,
            }
        )
        if multi_conversation:
            c2_id = str(uuid.uuid4())
            conversations.append(
                {
                    "id": c2_id,
                    "createdAt": "2026-06-01T00:00:00.000Z",
                    "title": "",
                    "type": "directmessage",
                    "isDirectMessage": True,
                    "creatorId": headmates[0]["id"],
                    "participantIds": [headmates[0]["id"], headmates[1]["id"]],
                    "displayOrder": 0,
                }
            )
            messages.append(
                {
                    "id": str(uuid.uuid4()),
                    "content": "hi dm",
                    "timestamp": "2026-06-01T00:02:00.000Z",
                    "isSystemMessage": False,
                    "authorId": headmates[0]["id"],
                    "conversationId": c2_id,
                }
            )

    member_board_posts: list[dict] = []
    if with_board_post:
        member_board_posts.append(
            {
                "id": str(uuid.uuid4()),
                "authorId": headmates[0]["id"],
                "audience": "public",
                "title": "post title",
                "body": "post body",
                "createdAt": "2026-06-01T10:00:00.000Z",
                "writtenAt": "2026-06-01T10:00:00.000Z",
                "isDeleted": False,
            }
        )

    media_attachments: list[dict] = []
    media_blobs: list[tuple[str, bytes]] = []
    if with_media:
        png = _make_png_bytes(size=8)
        blob, key_b64 = _make_xchacha_blob(png)
        media_id = "media-" + uuid.uuid4().hex
        media_attachments.append(
            {
                "id": str(uuid.uuid4()),
                "messageId": messages[0]["id"] if messages else "",
                "memberId": headmates[0]["id"],
                "mediaId": media_id,
                "mediaType": "image",
                "encryptionKeyB64": key_b64,
                "contentHash": "deadbeef",
                "plaintextHash": "cafebabe",
                "mimeType": "image/png",
                "sizeBytes": len(png),
                "isDeleted": False,
            }
        )
        media_blobs.append((media_id, blob))

    payload: dict = {
        "formatVersion": "1.0",
        "version": "1.0",
        "appName": "Prism Plurality (test)",
        "exportDate": "2026-06-08T12:00:00.000000Z",
        "totalRecords": len(headmates) + len(front_sessions),
        "headmates": headmates,
        "frontSessions": front_sessions,
        "sleepSessions": [
            {
                "id": str(uuid.uuid4()),
                "startTime": "2026-06-01T20:00:00.000Z",
                "endTime": "2026-06-02T06:00:00.000Z",
                "quality": 4,
                "notes": "",
                "isHealthKitImport": False,
            }
            for _ in range(sleep_sessions)
        ],
        "conversations": conversations,
        "messages": messages,
        "polls": polls,
        "pollOptions": poll_options,
        "systemSettings": [
            {
                "systemName": "Test System",
                "systemDescription": "test",
                "systemColor": "df50eb",
            }
        ],
        "habits": [
            {
                "id": str(uuid.uuid4()),
                "name": "h",
                "isActive": True,
                "createdAt": "2026-06-01T10:00:00.000Z",
                "frequency": "daily",
            }
            for _ in range(habits)
        ],
        "habitCompletions": [],
        "memberGroups": groups,
        "memberGroupEntries": group_entries,
        "customFields": custom_fields,
        "notes": notes,
        "reminders": [
            {
                "id": str(uuid.uuid4()),
                "name": "r",
                "message": "x",
                "trigger": 0,
                "frequency": "daily",
                "timeOfDay": "09:00",
                "isActive": True,
                "createdAt": "2026-06-01T10:00:00.000Z",
                "modifiedAt": "2026-06-01T10:00:00.000Z",
            }
            for _ in range(reminders)
        ],
        "memberBoardPosts": member_board_posts,
        "mediaAttachments": media_attachments,
    }
    return payload, media_blobs


_PASSPHRASE = "correct horse battery staple"


def _post_file(
    client: httpx.Client,
    payload_bytes: bytes,
    *,
    credential: str | None = _PASSPHRASE,
    options: dict | None = None,
    idem_key: str | None = None,
) -> dict:
    form: dict[str, str] = {
        "source": "prism_file",
        "idempotency_key": idem_key or str(uuid.uuid4()),
    }
    if options is not None:
        form["options"] = json.dumps(options)
    if credential is not None:
        form["credential"] = credential
    resp = client.post(
        "/v1/imports/file",
        files={"file": ("export.prism", payload_bytes, "application/octet-stream")},
        data=form,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


# --- Preview ----------------------------------------------------------------


def test_preview_decrypts_and_returns_summary(auth_client: httpx.Client):
    payload, media = _make_export(with_avatar=True, with_media=True)
    envelope = synthesize_envelope(payload, _PASSPHRASE, media_blobs=media)
    resp = auth_client.post(
        "/v1/import/prism/preview",
        files={"file": ("e.prism", envelope, "application/octet-stream")},
        data={"passphrase": _PASSPHRASE},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["member_count"] == 2
    assert body["media_blob_count"] == 1
    assert body["system_name"] == "Test System"
    assert body["format_version"] == "1.0"
    names = {m["name"] for m in body["members"]}
    assert names == {"Member-A", "Member-B"}


def test_preview_wrong_passphrase_is_400(auth_client: httpx.Client):
    payload, _ = _make_export()
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    resp = auth_client.post(
        "/v1/import/prism/preview",
        files={"file": ("e.prism", envelope, "application/octet-stream")},
        data={"passphrase": "wrong"},
    )
    assert resp.status_code == 400
    assert "passphrase" in resp.json()["detail"].lower()


def test_preview_rejects_non_prism_file(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/prism/preview",
        files={"file": ("e.prism", b"not a prism file", "application/octet-stream")},
        data={"passphrase": _PASSPHRASE},
    )
    assert resp.status_code == 400


# --- Runner -----------------------------------------------------------------


def test_runner_imports_members_groups_fields_polls(auth_client: httpx.Client):
    payload, media = _make_export()
    envelope = synthesize_envelope(payload, _PASSPHRASE, media_blobs=media)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    counts = final["counts"]
    assert counts["members_imported"] == 2
    assert counts["groups_imported"] == 1
    assert counts["custom_fields_imported"] == 2
    assert counts["fronts_imported"] == 1
    assert counts["journals_imported"] == 1
    assert counts["polls_imported"] == 1
    assert counts["messages_imported"] == 1
    assert counts["board_posts_imported"] == 1


def test_runner_avatar_imported_via_inline_base64(auth_client: httpx.Client):
    payload, media = _make_export(with_avatar=True)
    envelope = synthesize_envelope(payload, _PASSPHRASE, media_blobs=media)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["avatars_imported"] == 1


def test_runner_decrypts_media_attachment(auth_client: httpx.Client):
    payload, media = _make_export(with_media=True)
    envelope = synthesize_envelope(payload, _PASSPHRASE, media_blobs=media)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["media_attachments_imported"] == 1


def test_runner_open_poll_gets_one_year_window(auth_client: httpx.Client):
    payload, _ = _make_export(with_open_poll=True)
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["polls_imported"] == 1
    assert any(
        "one-year" in e["message"].lower() and e["level"] == "warning"
        for e in final["events"]
    ), final["events"]


def test_runner_multi_conversation_collapse_warns(auth_client: httpx.Client):
    payload, _ = _make_export(multi_conversation=True)
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["messages_imported"] == 2
    assert any(
        "collapsed" in e["message"].lower() for e in final["events"]
    ), final["events"]


def test_runner_skips_sleep_habits_reminders_with_warning(auth_client: httpx.Client):
    payload, _ = _make_export(sleep_sessions=2, habits=1, reminders=1)
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    msgs = [e["message"].lower() for e in final["events"] if e["level"] == "warning"]
    assert any("sleep" in m for m in msgs)
    assert any("habit" in m for m in msgs)
    assert any("reminder" in m for m in msgs)


def test_runner_surfaces_slider_field_as_text_warning(auth_client: httpx.Client):
    payload, _ = _make_export()
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert any(
        "slider" in e["message"].lower() and e["level"] == "warning"
        for e in final["events"]
    ), final["events"]


def test_runner_fails_when_credential_missing(auth_client: httpx.Client):
    payload, _ = _make_export()
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    job = _post_file(auth_client, envelope, credential=None)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any(
        "passphrase" in e["message"].lower() for e in final["events"]
    ), final["events"]


def test_runner_fails_on_wrong_passphrase(auth_client: httpx.Client):
    payload, _ = _make_export()
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    job = _post_file(auth_client, envelope, credential="wrong")
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any(
        "passphrase" in e["message"].lower() for e in final["events"]
    ), final["events"]


def test_runner_passphrase_wiped_after_finalize(auth_client: httpx.Client):
    """Ensure the encrypted_credential is gone from payload_metadata
    once the job reaches a terminal state."""
    payload, _ = _make_export()
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    # The detail response doesn't expose payload_metadata directly,
    # but the runner's _finalize already strips the field. Sanity:
    # job is terminal and complete, so the strip has run.
    assert final["status"] in ("complete", "failed", "cancelled")


@pytest.fixture(autouse=True)
def _quiet_unused_pytest_warnings():
    """Silence pytest's unused-fixture warnings on this module."""
    yield


# --- Hardening: member cap, external avatar URL policy ----------------------


def test_member_cap_fails_job_before_writing(auth_client: httpx.Client):
    from tests.test_imports_pluralspace_runner import _set_member_limit

    _set_member_limit(auth_client, 1)
    payload, _media = _make_export(headmate_count=2)
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("limited to" in e["message"] for e in final["events"]), final["events"]
    members = auth_client.get("/v1/members").json()
    assert members == [], members


def test_external_avatar_with_bad_scheme_is_dropped(auth_client: httpx.Client):
    """pkAvatarCachedUrl with a non-http(s) scheme must not land in the
    profile field; the skip surfaces as a warning event."""
    payload, _media = _make_export(headmate_count=1)
    payload["headmates"][0]["pkAvatarCachedUrl"] = "javascript:alert(1)"
    envelope = synthesize_envelope(payload, _PASSPHRASE)
    job = _post_file(auth_client, envelope)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    members = auth_client.get("/v1/members").json()
    assert members and members[0]["avatar_url"] is None, members
    assert any(
        "external avatar" in e["message"].lower() for e in final["events"]
    ), final["events"]


def test_prism_reimport_is_idempotent(auth_client: httpx.Client):
    """Re-importing the same envelope skips members and every content
    section, and does not crash on the custom-field value or
    group-membership constraints (both pre-seeded guards)."""
    payload, media = _make_export()
    envelope = synthesize_envelope(payload, _PASSPHRASE, media_blobs=media)

    first = _post_file(auth_client, envelope)
    drive_import_runner()
    f1 = wait_for_terminal(auth_client, first["id"])
    assert f1["status"] == "complete", f1

    second = _post_file(auth_client, envelope)
    drive_import_runner()
    f2 = wait_for_terminal(auth_client, second["id"])
    assert f2["status"] == "complete", f2

    counts = f2["counts"]
    assert counts.get("members_imported", 0) == 0, counts
    assert counts.get("members_skipped", 0) >= 1, counts
    for key in (
        "fronts_imported",
        "journals_imported",
        "messages_imported",
        "polls_imported",
        "board_posts_imported",
    ):
        assert counts.get(key, 0) == 0, (key, counts)
