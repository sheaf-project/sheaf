"""Passkey (WebAuthn) relying-party resolution and the availability rule.

A WebAuthn credential is bound forever to one relying-party ID, so before
any ceremony can run the instance has to know its own name. This module is
the single answer to "what is this instance's RP ID, and may passkeys be
offered here at all". Everything else in the feature asks it; nothing else
derives the value.

The rule, from the passkey design:

* The RP ID is the host of ``SHEAF_BASE_URL``. An operator may override it
  with ``PASSKEY_RP_ID``, but only to a registrable parent of that host
  (``example.net`` for an instance at ``sheaf.example.net``), which is what
  lets one credential serve several subdomains. Anything else is refused.
* The scheme must be ``https``. Browsers require a secure context for
  WebAuthn, so a plain-HTTP instance cannot have this feature; the one
  exception is the loopback names browsers themselves treat as secure,
  which is what lets the test stack and a local dev server use it.
* When the rule cannot be satisfied the feature is *unavailable and says
  why*. It must never be discovered at ceremony time.

The RP ID is never derived from the request's ``Host`` header. Behind a
proxy that header is whatever reached the app, and using it would let any
host that can reach the app mint credentials for itself.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

# Hostnames browsers treat as a secure context over plain HTTP. Deliberately
# the literal names and nothing wider: ``*.localhost`` and private ranges
# are not exempt in every browser and an instance on one of those needs
# HTTPS like any other.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Reasons the feature is unavailable. Stable strings: they are what
# ``/v1/auth/config`` will report to a client, which shows the matching copy.
REASON_NO_BASE_URL = "no_base_url"
REASON_MALFORMED_BASE_URL = "malformed_base_url"
REASON_INSECURE_BASE_URL = "insecure_base_url"
REASON_RP_ID_INVALID = "rp_id_invalid"
REASON_RP_ID_NOT_PARENT_OF_HOST = "rp_id_not_parent_of_host"
REASON_RP_ID_PUBLIC_SUFFIX = "rp_id_public_suffix"
# The operator's master switch is off. Not a configuration fault, so listed
# apart from the rule's own refusals.
REASON_DISABLED = "disabled"

# What a browser serialises an origin without: the scheme's default port.
# `https://sheaf.example:443` is the origin `https://sheaf.example` to the
# browser, and py_webauthn compares origins as strings, so the server has to
# apply the same rule or every ceremony on such a base URL fails.
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True, slots=True)
class RelyingParty:
    """What a ceremony needs to know about this instance.

    ``rp_id`` is the value the browser binds the credential to. ``origin`` is
    the exact origin a ceremony's client data must carry (scheme, host and
    an explicit port if the base URL has one), checked server-side.
    """

    rp_id: str
    origin: str


@dataclass(frozen=True, slots=True)
class RelyingPartyResolution:
    """The outcome of the availability rule: a relying party, or a reason."""

    rp: RelyingParty | None
    reason: str | None

    @property
    def available(self) -> bool:
        return self.rp is not None


def _unavailable(reason: str) -> RelyingPartyResolution:
    return RelyingPartyResolution(rp=None, reason=reason)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _ascii_host(host: str) -> str | None:
    """The host as a browser puts it in an origin and an RP ID: lowercase
    ASCII, with any internationalised label in its punycode form. None when
    the name cannot be encoded at all."""
    if host.isascii():
        return host.lower()
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def _is_public_suffix(candidate: str) -> bool:
    """True when the candidate is itself a public suffix (``co.uk``,
    ``github.io``), which no credential can be bound to.

    Browsers refuse an RP ID that is a public suffix, so accepting one here
    would advertise passkeys the browser then rejects at ceremony time, which
    is exactly the "looks broken" failure the availability rule exists to
    prevent. The private section of the list counts too: ``github.io`` is a
    public suffix for this purpose in every browser that ships the list.
    """
    psl = _public_suffix_list()
    return psl.publicsuffix(candidate) == candidate and psl.privatesuffix(candidate) is None


_PSL = None


def _public_suffix_list():
    # Built once: the bundled list is a third of a megabyte to parse, and the
    # rule runs on every config read and every passkey request.
    global _PSL
    if _PSL is None:
        from publicsuffixlist import PublicSuffixList

        _PSL = PublicSuffixList()
    return _PSL


def _valid_rp_id(candidate: str) -> bool:
    """A bare host: lowercase labels, no scheme, port, path or brackets.

    Deliberately strict about shape rather than about DNS validity: the value
    is operator configuration, and a typo here should fail the rule loudly
    rather than produce credentials bound to a name nothing serves.
    """
    if not candidate or len(candidate) > 253:
        return False
    if candidate != candidate.strip().lower():
        return False
    if "/" in candidate or ":" in candidate or "@" in candidate or " " in candidate:
        return False
    labels = candidate.split(".")
    if any(not label or label.startswith("-") or label.endswith("-") for label in labels):
        return False
    return all(ch.isalnum() or ch == "-" for label in labels for ch in label)


def resolve_relying_party(base_url: str, rp_id_override: str = "") -> RelyingPartyResolution:
    """Apply the availability rule to a base URL and an optional override.

    Pure: takes the two configuration values and returns the answer, so the
    rule can be tested without a server and reused by both the startup
    check and the config endpoint. Callers wanting the instance's live
    answer use ``current_relying_party``.
    """
    base_url = (base_url or "").strip()
    if not base_url:
        return _unavailable(REASON_NO_BASE_URL)

    try:
        parts = urlsplit(base_url)
        raw_host = parts.hostname  # lowercased, IPv6 brackets stripped
        port = parts.port  # raises ValueError on a non-numeric port
    except ValueError:
        return _unavailable(REASON_MALFORMED_BASE_URL)
    if parts.scheme not in ("http", "https") or not raw_host:
        return _unavailable(REASON_MALFORMED_BASE_URL)
    host = _ascii_host(raw_host)
    if host is None:
        return _unavailable(REASON_MALFORMED_BASE_URL)

    if parts.scheme != "https" and host not in LOOPBACK_HOSTS:
        return _unavailable(REASON_INSECURE_BASE_URL)

    rp_id = host
    override = (rp_id_override or "").strip()
    if override:
        override = _ascii_host(override) or override
        if not _valid_rp_id(override):
            return _unavailable(REASON_RP_ID_INVALID)
        # Equal to the host, or a registrable parent of it. An IP literal has
        # no parents: "0.0.1" is a dotted suffix of "127.0.0.1" but not a
        # domain anything can be bound to, so for a literal only equality
        # passes.
        if override != host and (_is_ip_literal(host) or not host.endswith("." + override)):
            return _unavailable(REASON_RP_ID_NOT_PARENT_OF_HOST)
        # A dotted suffix is not enough: "co.uk" is a parent of
        # "sheaf.example.co.uk" by string but a public suffix by the list every
        # browser ships, and a browser refuses to bind a credential to one.
        if override != host and _is_public_suffix(override):
            return _unavailable(REASON_RP_ID_PUBLIC_SUFFIX)
        rp_id = override

    # Rebuild the origin from the parsed parts rather than echoing the
    # configured string: a trailing path, mixed-case host, internationalised
    # label or default port in SHEAF_BASE_URL must not leak into the origin
    # comparison. The port is kept only when it is not the scheme's default,
    # because that is how a browser serialises the origin it signs.
    origin_host = f"[{host}]" if ":" in host else host
    origin = f"{parts.scheme}://{origin_host}"
    if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
        origin = f"{origin}:{port}"

    return RelyingPartyResolution(rp=RelyingParty(rp_id=rp_id, origin=origin), reason=None)


def current_relying_party() -> RelyingPartyResolution:
    """The availability rule applied to this instance's settings."""
    # Imported here rather than at module level: sheaf.config runs this
    # rule in its startup checks, and a top-level import both ways would
    # be a cycle.
    from sheaf.config import settings

    if not settings.passkeys_enabled:
        return _unavailable(REASON_DISABLED)
    return resolve_relying_party(settings.sheaf_base_url, settings.passkey_rp_id)
