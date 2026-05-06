"""PluralKit live-API fetch.

Pulls a PluralKit system, members, groups, and switches via the public v2
API and returns them in the same shape as a PK file export, so that the
single importer in `pk_import.py` can consume either source uniformly.

The supplied token is request-scoped — never logged, never persisted, just
forwarded to PK on a single request. PK's auth header is the bare token
(no `Bearer` prefix), per their published API docs.

Rate limiting: PK allows ~2 requests/second per token. We sleep ~600ms
between page requests to stay comfortably under that, which adds latency
but avoids the need for any retry/backoff complexity. A system with
5000 switches paginates as 50 sequential page fetches (~30s); the user
already understands an "import" can take a moment.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from sheaf.config import settings

logger = logging.getLogger("sheaf.import.pk_api")

PK_API_BASE = "https://api.pluralkit.me/v2"
SWITCHES_PAGE_SIZE = 100
RATE_LIMIT_DELAY_SECONDS = 0.6
REQUEST_TIMEOUT_SECONDS = 30.0


class PKApiError(Exception):
    """Raised when the PluralKit API returns an error or is unreachable."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _user_agent() -> str:
    # PK asks identifying clients to set a User-Agent. We don't have a
    # generic instance-contact field, so report Sheaf and link to the
    # public repo for any abuse follow-up.
    base_url = getattr(settings, "sheaf_base_url", "") or "https://github.com/sheaf-project/sheaf"
    return f"Sheaf-PluralKit-Importer ({base_url})"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": token,
        "User-Agent": _user_agent(),
        "Accept": "application/json",
    }


async def _get(client: httpx.AsyncClient, path: str, token: str, **params: Any) -> Any:
    """Issue one GET against the PK API, mapping HTTP errors to PKApiError."""
    try:
        resp = await client.get(
            f"{PK_API_BASE}{path}",
            headers=_headers(token),
            params=params or None,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise PKApiError(f"Could not reach PluralKit API: {exc}") from exc

    if resp.status_code == 401:
        raise PKApiError("PluralKit token rejected (401). Check the token and try again.", 401)
    if resp.status_code == 403:
        raise PKApiError("PluralKit denied access (403). The token may lack permission.", 403)
    if resp.status_code == 404:
        raise PKApiError("PluralKit returned 404. The system or resource was not found.", 404)
    if resp.status_code == 429:
        raise PKApiError("PluralKit rate-limited the import. Try again in a minute.", 429)
    if resp.status_code >= 500:
        raise PKApiError(
            f"PluralKit server error ({resp.status_code}). Try again later.",
            resp.status_code,
        )
    if resp.status_code >= 400:
        raise PKApiError(
            f"PluralKit returned {resp.status_code}: {resp.text[:200]}",
            resp.status_code,
        )

    try:
        return resp.json()
    except ValueError as exc:
        raise PKApiError("PluralKit returned a non-JSON response.") from exc


async def fetch_export(token: str, *, include_switches: bool = True) -> dict:
    """Pull a complete PK system snapshot, returning it in export-file shape.

    The returned dict has the same top-level keys as a PluralKit data
    export — `id`, `name`, `members`, `groups`, `switches`, etc. — so the
    single importer pipeline can consume both file and live-API sources.

    `include_switches=False` skips the (potentially many-paged) switches
    fetch and is used by the preview endpoint, where we only want a count
    of pages and a date range. The preview fetches one page and reports
    `switch_count` as either the exact size when <=100, or "100+" via a
    flag (see `pk_import.preview`).
    """
    async with httpx.AsyncClient() as client:
        system = await _get(client, "/systems/@me", token)
        await asyncio.sleep(RATE_LIMIT_DELAY_SECONDS)

        members = await _get(client, "/systems/@me/members", token)
        await asyncio.sleep(RATE_LIMIT_DELAY_SECONDS)

        groups = await _get(client, "/systems/@me/groups", token, with_members="true")
        await asyncio.sleep(RATE_LIMIT_DELAY_SECONDS)

        switches: list[dict] = []
        if include_switches:
            switches = await _fetch_all_switches(client, token)

    # Reshape into export-file format. PK API returns members/groups/switches
    # at sibling paths; the export file collapses them into one document.
    return {
        **system,
        "members": members,
        "groups": groups,
        "switches": switches,
        "version": 2,
    }


async def fetch_switch_sample(token: str) -> tuple[list[dict], bool]:
    """Pull just one page of switches for the preview screen.

    Returns `(switches, has_more)` where `has_more` is true if the page
    was full (100 entries) and there may be older switches. Lets the
    preview show "100+" without making the user wait for full pagination.
    """
    async with httpx.AsyncClient() as client:
        page = await _get(
            client,
            "/systems/@me/switches",
            token,
            limit=SWITCHES_PAGE_SIZE,
        )
    if not isinstance(page, list):
        return [], False
    return page, len(page) >= SWITCHES_PAGE_SIZE


async def _fetch_all_switches(client: httpx.AsyncClient, token: str) -> list[dict]:
    """Walk PK switch pagination newest-to-oldest until exhausted."""
    all_switches: list[dict] = []
    before: str | None = None

    while True:
        params: dict[str, Any] = {"limit": SWITCHES_PAGE_SIZE}
        if before is not None:
            params["before"] = before

        page = await _get(client, "/systems/@me/switches", token, **params)
        if not isinstance(page, list) or not page:
            break

        all_switches.extend(page)

        if len(page) < SWITCHES_PAGE_SIZE:
            break

        # PK pagination: `before` returns switches strictly older than the
        # given timestamp. The last entry of the current page is the oldest.
        oldest_ts = page[-1].get("timestamp")
        if not oldest_ts or oldest_ts == before:
            # Defensive: if the API ever stops advancing, bail rather than loop.
            logger.warning("PK switches pagination stalled at %s, stopping.", oldest_ts)
            break
        before = oldest_ts

        await asyncio.sleep(RATE_LIMIT_DELAY_SECONDS)

    return all_switches
