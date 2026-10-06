"""The app-to-site association documents, without a server stack.

Pins the parsing of `PASSKEY_ANDROID_APPS` / `PASSKEY_IOS_APPS` (what is
accepted, what is dropped and why), the shape of the two documents, and the
route handlers' one decision: nothing configured means 404, not an empty
document.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.exceptions import HTTPException

from sheaf.auth.app_associations import (
    AndroidApp,
    apple_association_document,
    assetlinks_document,
    parse_android_apps,
    parse_ios_apps,
)

FP1 = ":".join(["AB"] * 32)
FP2 = ":".join(["01"] * 32)


# --- Android -----------------------------------------------------------------


def test_one_android_app_with_one_fingerprint():
    apps, errors = parse_android_apps(f"sh.sheaf.app={FP1}")
    assert errors == []
    assert apps == [AndroidApp(package_name="sh.sheaf.app", fingerprints=(FP1,))]


def test_several_apps_and_several_fingerprints():
    apps, errors = parse_android_apps(f" sh.sheaf.app={FP1}|{FP2} , org.example.fork={FP2} ")
    assert errors == []
    assert [a.package_name for a in apps] == ["sh.sheaf.app", "org.example.fork"]
    assert apps[0].fingerprints == (FP1, FP2)
    assert apps[1].fingerprints == (FP2,)


def test_fingerprint_case_is_normalised_upwards():
    apps, errors = parse_android_apps(f"sh.sheaf.app={FP1.lower()}")
    assert errors == []
    assert apps[0].fingerprints == (FP1,)


def test_empty_android_setting_is_nothing_not_an_error():
    assert parse_android_apps("") == ([], [])
    assert parse_android_apps(" , ,") == ([], [])


@pytest.mark.parametrize(
    "entry",
    [
        "sh.sheaf.app",  # no fingerprint
        "sh.sheaf.app=",  # empty fingerprint
        f"={FP1}",  # no package
        f"sheafapp={FP1}",  # single-segment package
        f"sh.9sheaf.app={FP1}",  # segment starts with a digit
        f"sh.sheaf.app={FP1[:-3]}",  # 31 bytes
        f"sh.sheaf.app={FP1.replace(':', '')}",  # no colons
        f"sh.sheaf.app={FP1}|nope",  # one good, one bad: whole entry dropped
    ],
)
def test_a_bad_android_entry_is_dropped_with_a_reason(entry):
    apps, errors = parse_android_apps(f"{entry},org.example.ok={FP2}")
    assert [a.package_name for a in apps] == ["org.example.ok"]
    assert len(errors) == 1
    assert entry in errors[0]


def test_assetlinks_document_grants_only_the_credential_relation():
    apps, _ = parse_android_apps(f"sh.sheaf.app={FP1}|{FP2}")
    doc = assetlinks_document(apps)
    assert doc == [
        {
            "relation": ["delegate_permission/common.get_login_creds"],
            "target": {
                "namespace": "android_app",
                "package_name": "sh.sheaf.app",
                "sha256_cert_fingerprints": [FP1, FP2],
            },
        }
    ]
    # App Links (deep links) are a separate decision and not volunteered.
    assert "delegate_permission/common.handle_all_urls" not in json.dumps(doc)


# --- iOS ------------------------------------------------------------------------


def test_ios_apps_parse_and_keep_order():
    apps, errors = parse_ios_apps(" ABCDE12345.sh.sheaf.app, ZYXWV98765.org.example.fork ")
    assert errors == []
    assert apps == ["ABCDE12345.sh.sheaf.app", "ZYXWV98765.org.example.fork"]


@pytest.mark.parametrize(
    "entry",
    [
        "sh.sheaf.app",  # no team id
        "ABCDE1234.sh.sheaf.app",  # nine-character team id
        "abcde12345.sh.sheaf.app",  # lower-case team id
        "ABCDE12345.",  # no bundle id
        "ABCDE12345",  # team id only
    ],
)
def test_a_bad_ios_entry_is_dropped_with_a_reason(entry):
    apps, errors = parse_ios_apps(f"{entry},ABCDE12345.org.example.ok")
    assert apps == ["ABCDE12345.org.example.ok"]
    assert len(errors) == 1
    assert entry in errors[0]


def test_apple_document_is_webcredentials_only():
    doc = apple_association_document(["ABCDE12345.sh.sheaf.app"])
    assert doc == {"webcredentials": {"apps": ["ABCDE12345.sh.sheaf.app"]}}
    assert "applinks" not in doc


# --- the routes ---------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def test_routes_404_when_nothing_is_configured(monkeypatch):
    from sheaf.config import settings
    from sheaf.main import android_assetlinks, apple_app_site_association

    monkeypatch.setattr(settings, "passkey_android_apps", "")
    monkeypatch.setattr(settings, "passkey_ios_apps", "")
    with pytest.raises(HTTPException) as a:
        _run(android_assetlinks())
    with pytest.raises(HTTPException) as b:
        _run(apple_app_site_association())
    assert a.value.status_code == 404
    assert b.value.status_code == 404


def test_routes_404_when_every_entry_is_malformed(monkeypatch):
    from sheaf.config import settings
    from sheaf.main import android_assetlinks, apple_app_site_association

    monkeypatch.setattr(settings, "passkey_android_apps", "nope")
    monkeypatch.setattr(settings, "passkey_ios_apps", "nope")
    with pytest.raises(HTTPException):
        _run(android_assetlinks())
    with pytest.raises(HTTPException):
        _run(apple_app_site_association())


def test_routes_serve_json_documents_when_configured(monkeypatch):
    from sheaf.config import settings
    from sheaf.main import android_assetlinks, apple_app_site_association

    monkeypatch.setattr(settings, "passkey_android_apps", f"sh.sheaf.app={FP1}")
    monkeypatch.setattr(settings, "passkey_ios_apps", "ABCDE12345.sh.sheaf.app")

    android = _run(android_assetlinks())
    assert android.status_code == 200
    assert android.media_type == "application/json"
    assert android.headers["cache-control"] == "public, max-age=3600"
    assert json.loads(android.body)[0]["target"]["package_name"] == "sh.sheaf.app"

    apple = _run(apple_app_site_association())
    assert apple.status_code == 200
    assert apple.media_type == "application/json"
    assert json.loads(apple.body) == {"webcredentials": {"apps": ["ABCDE12345.sh.sheaf.app"]}}
