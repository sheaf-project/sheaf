"""App-to-site association documents for native passkey clients.

A native app can only use a passkey against a web origin that has vouched
for it. Android and iOS both do this with a small JSON document the site
publishes under `/.well-known/`, and both documents are built here from two
operator settings, so an instance can vouch for the apps its operator
chooses: the published Sheaf apps on the hosted instance, a fork's own build
on a selfhosted one.

The two platforms are not symmetric, and the selfhosting docs say so rather
than implying parity:

* **Android** (`assetlinks.json`) resolves the association at ceremony time
  by fetching the document from the RP ID. The app needs no build-time
  knowledge of the domain, so a selfhoster who sets `PASSKEY_ANDROID_APPS` to
  the published app's package and signing fingerprint gets passkeys in that
  app against their own instance.
* **iOS** (`apple-app-site-association`) also fetches the document, but the
  app's associated-domains entitlement is baked in at build time, so a
  published app can only use passkeys against domains its maintainers
  compiled into it. `PASSKEY_IOS_APPS` is still needed on those domains; it
  cannot make an arbitrary selfhosted domain work without a custom build.

Nothing here is secret. Both documents are public by specification, and
anything in them is already visible in the app store listing (package name,
bundle id) or the signed APK (certificate fingerprint). Malformed entries
are dropped with a boot warning rather than published, because a document
with a bad fingerprint in it fails the platform's check in a way that is
indistinguishable from "no document", and the warning is the only place the
operator would ever learn why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Java package naming as Android enforces it: dotted segments, each starting
# with a letter, letters/digits/underscore after. At least two segments.
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
# SHA-256 signing-certificate fingerprint as `keytool` and the Play console
# print it: 32 colon-separated hex bytes. Case is normalised to upper.
_FINGERPRINT_RE = re.compile(r"^([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$")
# Apple app identifier: the ten-character team id, a dot, the bundle id.
_IOS_APP_RE = re.compile(r"^[A-Z0-9]{10}\.[A-Za-z0-9][A-Za-z0-9.-]*$")


@dataclass(frozen=True, slots=True)
class AndroidApp:
    package_name: str
    fingerprints: tuple[str, ...]


def parse_android_apps(raw: str) -> tuple[list[AndroidApp], list[str]]:
    """Parse `PASSKEY_ANDROID_APPS`.

    Format: comma-separated entries of `package.name=FINGERPRINT`, where a
    package with several signing certificates (a debug and a release key,
    say) lists them separated by `|`. Returns the valid apps and a list of
    messages for the entries that were dropped, so the boot check can say
    exactly which one is wrong.
    """
    apps: list[AndroidApp] = []
    errors: list[str] = []
    for entry in _entries(raw):
        package, sep, fingerprints_raw = entry.partition("=")
        package = package.strip()
        if not sep or not fingerprints_raw.strip():
            errors.append(
                f"{entry!r}: expected package.name=FINGERPRINT[|FINGERPRINT...]"
            )
            continue
        if not _PACKAGE_RE.match(package):
            errors.append(f"{entry!r}: {package!r} is not a valid Android package name")
            continue
        fingerprints: list[str] = []
        bad = False
        for fp in fingerprints_raw.split("|"):
            fp = fp.strip()
            if not _FINGERPRINT_RE.match(fp):
                errors.append(
                    f"{entry!r}: {fp!r} is not a SHA-256 certificate fingerprint "
                    "(32 colon-separated hex bytes)"
                )
                bad = True
                break
            fingerprints.append(fp.upper())
        if bad:
            continue
        apps.append(AndroidApp(package_name=package, fingerprints=tuple(fingerprints)))
    return apps, errors


def parse_ios_apps(raw: str) -> tuple[list[str], list[str]]:
    """Parse `PASSKEY_IOS_APPS`: comma-separated `TEAMID.bundle.identifier`."""
    apps: list[str] = []
    errors: list[str] = []
    for entry in _entries(raw):
        if not _IOS_APP_RE.match(entry):
            errors.append(
                f"{entry!r}: expected TEAMID.bundle.identifier (ten-character team id, "
                "a dot, the bundle id)"
            )
            continue
        apps.append(entry)
    return apps, errors


def _entries(raw: str) -> list[str]:
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


def assetlinks_document(apps: list[AndroidApp]) -> list[dict]:
    """The body of `/.well-known/assetlinks.json`.

    Only the credential relation is granted. `handle_all_urls` (App Links,
    the deep-link relation) is a separate decision for the app's maintainers
    and would make the app claim every URL on the instance; passkeys do not
    need it and this document does not volunteer it.
    """
    return [
        {
            "relation": ["delegate_permission/common.get_login_creds"],
            "target": {
                "namespace": "android_app",
                "package_name": app.package_name,
                "sha256_cert_fingerprints": list(app.fingerprints),
            },
        }
        for app in apps
    ]


def apple_association_document(app_ids: list[str]) -> dict:
    """The body of `/.well-known/apple-app-site-association`.

    `webcredentials` is the passkey (and password autofill) association.
    `applinks` (Universal Links) is deliberately absent for the same reason
    `handle_all_urls` is above.
    """
    return {"webcredentials": {"apps": list(app_ids)}}
