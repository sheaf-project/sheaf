"""Tests for the streaming export builder.

End-to-end behaviour (create job + worker builds it + download
returns a valid zip) is covered by existing tests against the
running stack. This file focuses on the tempfile lifecycle: a
failed build must not strand temp zips.
"""

from __future__ import annotations

import asyncio
import os


def test_streaming_export_cleans_up_tempfile_on_failure(monkeypatch):
    """If the build raises mid-assembly, the tempfile should not be
    left behind. We poke the export_all function to raise after a
    tempfile would have been created, then count files in the build
    tmpdir."""
    from sheaf.api.v1 import export as export_api
    from sheaf.services.export_builder import _assemble_zip_to_tempfile
    from sheaf.config import settings

    async def _boom(**_kwargs):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(export_api, "export_all", _boom)

    tmp_dir = settings.export_build_tmp_dir or None

    async def _go():
        try:
            await _assemble_zip_to_tempfile(
                db=None, user=None, include_images=False,
            )
        except RuntimeError:
            return
        raise AssertionError("_assemble_zip_to_tempfile did not raise")

    asyncio.run(_go())

    # Listing the tmpdir for orphans: this is best-effort because the
    # system tempdir is shared, but our prefix is distinctive.
    candidate = tmp_dir or "/tmp"
    orphans = [
        f for f in os.listdir(candidate)
        if f.startswith("sheaf-export-") and f.endswith(".zip")
    ]
    assert orphans == [], f"orphan tempfile(s) left behind: {orphans}"
