"""SSRF guard for outbound webhook + ntfy URLs.

Pure-Python checks against `assert_url_safe`. Network is not actually
contacted; we only verify the resolver/classifier rejects disallowed
destinations.
"""

from __future__ import annotations

import pytest

from sheaf.services.notifications.safe_http import SsrfRejected, assert_url_safe


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/foo",
        "http://[::1]/foo",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://100.64.0.1/",  # CGN
        "http://0.0.0.0/",
        "ftp://example.com/x",  # bad scheme
    ],
)
def test_disallowed_urls_rejected(url: str):
    with pytest.raises(SsrfRejected):
        assert_url_safe(url)


def test_public_url_accepted():
    # example.com resolves to a public unicast IP. If your CI has DNS
    # filtering that resolves it to RFC1918, this test will (correctly) fail.
    assert_url_safe("https://example.com/webhook")


def test_missing_host_rejected():
    with pytest.raises(SsrfRejected):
        assert_url_safe("https:///foo")
