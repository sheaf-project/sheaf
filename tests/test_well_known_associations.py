"""The association documents over the API, in the default test stack.

The stack configures no native apps, so both documents must be a clean 404
(not an empty document, not HTML), reachable without any auth, and must not
have disturbed security.txt, which lives next door under the same prefix.
"""

import httpx


def test_assetlinks_404_when_no_android_app_is_configured(client: httpx.Client):
    resp = client.get("/.well-known/assetlinks.json")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_apple_association_404_when_no_ios_app_is_configured(client: httpx.Client):
    resp = client.get("/.well-known/apple-app-site-association")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_security_txt_is_still_served_beside_them(client: httpx.Client):
    resp = client.get("/.well-known/security.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
