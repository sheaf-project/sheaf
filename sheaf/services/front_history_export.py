"""Standalone front-history export: CSV / JSON / ICS.

A lighter-weight companion to the full account export. Where the native /
OpenPlural exports produce a whole-account zip, this produces a single
file containing only the system's front history, in a format the user
picks:

* ``fronts_csv``  - one row per front, for spreadsheets / analysis.
* ``fronts_json`` - structured, self-describing, machine-readable.
* ``fronts_ics``  - an iCalendar feed (one VEVENT per front) to drop into
  a calendar app and see fronting history on a timeline.

These run through the same async export-job machinery as the full export
(``ExportJob.format`` carries the value), so the step-up auth gate, rate
limit, completion email, download, and cleanup all apply unchanged. The
only difference is the artefact is a single file, not a zip.

Encryption discipline matches the full export: ``custom_status`` and
member names are decrypted only here, on the way out, via the same
helpers the account export uses.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.crypto import decrypt
from sheaf.encrypted_fields import front_custom_status_aad
from sheaf.models.front import Front
from sheaf.models.system import System
from sheaf.services.members import member_plaintext

# The ExportJob.format values handled here. Kept as a tuple so callers can
# branch with `job.format in FRONT_HISTORY_FORMATS`.
FRONT_HISTORY_FORMATS = ("fronts_csv", "fronts_json", "fronts_ics")

_FORMAT_EXT = {"fronts_csv": "csv", "fronts_json": "json", "fronts_ics": "ics"}


def format_extension(fmt: str) -> str:
    """File extension for a front-history format (no leading dot)."""
    return _FORMAT_EXT[fmt]


async def load_front_history(db: AsyncSession, system: System) -> list[dict[str, Any]]:
    """Load and decrypt a system's fronts in chronological order.

    Member names and custom_status are decrypted here (owner's own data).
    Members are eager-loaded to avoid an N+1, and their names are sorted
    so a co-fronting summary is stable.
    """
    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(Front.system_id == system.id)
        .order_by(Front.started_at.asc())
    )
    rows: list[dict[str, Any]] = []
    for f in result.scalars().all():
        names = sorted(
            (member_plaintext(m)[0] for m in f.members), key=str.casefold
        )
        rows.append(
            {
                "id": str(f.id),
                "started_at": f.started_at,
                "ended_at": f.ended_at,
                "members": names,
                "custom_status": (
                    decrypt(f.custom_status, aad=front_custom_status_aad(f.id))
                    if f.custom_status
                    else None
                ),
            }
        )
    return rows


def serialize_front_history(
    rows: list[dict[str, Any]],
    system_name: str | None,
    fmt: str,
    exported_at: datetime,
) -> bytes:
    """Render already-loaded/decrypted front rows to the requested format."""
    if fmt == "fronts_csv":
        return _build_csv(rows)
    if fmt == "fronts_json":
        return _build_json(rows, system_name, exported_at)
    if fmt == "fronts_ics":
        return _build_ics(rows, system_name, exported_at)
    raise ValueError(f"unknown front-history format: {fmt!r}")


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection.

    A CSV cell beginning with =, +, -, @ (or a leading control char) can be
    executed as a formula by Excel / Sheets when the file is opened. Since a
    front-history export is meant to be shared, the recipient's spreadsheet
    must be safe even though the content (member names, custom_status) is the
    user's own free text. Prefixing with a single quote makes the cell render
    as literal text. Numeric / ISO-timestamp columns are never user free text,
    so only the text columns are guarded.
    """
    if value and value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


def _hms(seconds: int) -> str:
    """Hours:MM:SS, with hours unbounded (a front can span days)."""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _build_csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "started_at",
            "ended_at",
            "duration_seconds",
            "duration",
            "member_count",
            "members",
            "custom_status",
        ]
    )
    for r in rows:
        started = r["started_at"]
        ended = r["ended_at"]
        if ended is not None:
            secs = int((ended - started).total_seconds())
            duration_seconds: int | str = secs
            duration = _hms(secs)
        else:
            # Ongoing front: leave duration blank rather than guess an end.
            duration_seconds = ""
            duration = ""
        writer.writerow(
            [
                started.isoformat(),
                ended.isoformat() if ended else "",
                duration_seconds,
                duration,
                len(r["members"]),
                _csv_safe("; ".join(r["members"])),
                _csv_safe(r["custom_status"] or ""),
            ]
        )
    # Prepend a UTF-8 BOM so spreadsheet apps (Excel) read non-ASCII member
    # names correctly. The csv module handles quoting of embedded commas /
    # newlines / quotes in custom_status.
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _build_json(
    rows: list[dict[str, Any]], system_name: str | None, exported_at: datetime
) -> bytes:
    payload = {
        "sheaf_front_history_version": "1",
        "exported_at": exported_at.isoformat(),
        "system_name": system_name,
        "front_count": len(rows),
        "fronts": [
            {
                "id": r["id"],
                "started_at": r["started_at"].isoformat(),
                "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
                "members": r["members"],
                "custom_status": r["custom_status"],
            }
            for r in rows
        ],
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def _ics_escape(text: str) -> str:
    """Escape a TEXT value per RFC 5545 (backslash, newline, comma, semicolon).

    Carriage returns are normalised to the escaped newline: a bare or CRLF
    CR embedded in user content (a member name, custom_status, or the system
    name) would otherwise terminate the content line and let the value
    inject its own calendar properties or a whole VEVENT into anyone's
    calendar that imports the feed. CRLF is collapsed first so it yields a
    single escaped newline, matching how a lone newline is handled.
    """
    return (
        text.replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _ics_fold(line: str) -> str:
    """Fold a content line to <=75 octets per RFC 5545, on UTF-8 char
    boundaries (continuation lines begin with a single space)."""
    if len(line.encode("utf-8")) <= 75:
        return line
    chunks: list[bytes] = []
    cur = b""
    for ch in line:
        b = ch.encode("utf-8")
        # Leave room for the leading space that continuation lines carry.
        if len(cur) + len(b) > 74 and cur:
            chunks.append(cur)
            cur = b" " + b
        else:
            cur += b
    chunks.append(cur)
    return "\r\n".join(c.decode("utf-8") for c in chunks)


def _dt_utc(dt: datetime) -> str:
    """An aware datetime as a UTC iCalendar timestamp (YYYYMMDDTHHMMSSZ)."""
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _build_ics(
    rows: list[dict[str, Any]], system_name: str | None, exported_at: datetime
) -> bytes:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sheaf//Front History//EN",
        "CALSCALE:GREGORIAN",
    ]
    if system_name:
        lines.append(
            _ics_fold("X-WR-CALNAME:" + _ics_escape(f"{system_name} front history"))
        )
    stamp = _dt_utc(exported_at)
    for r in rows:
        summary = "; ".join(r["members"]) or "(no member)"
        ended = r["ended_at"]
        if ended is None:
            # iCalendar needs a DTEND; mark the open front as ongoing and
            # span it to the export time so it renders as a real interval.
            summary += " (ongoing)"
            end_dt = exported_at
        else:
            end_dt = ended
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{r['id']}@sheaf")
        lines.append(f"DTSTAMP:{stamp}")
        lines.append(f"DTSTART:{_dt_utc(r['started_at'])}")
        lines.append(f"DTEND:{_dt_utc(end_dt)}")
        lines.append(_ics_fold("SUMMARY:" + _ics_escape(summary)))
        if r["custom_status"]:
            lines.append(_ics_fold("DESCRIPTION:" + _ics_escape(r["custom_status"])))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    # iCalendar mandates CRLF line endings.
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")
