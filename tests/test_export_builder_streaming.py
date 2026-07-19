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
    from sheaf.config import settings
    from sheaf.services.export_builder import _assemble_zip_to_tempfile

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


def test_put_path_survives_cross_device_rename(monkeypatch, tmp_path):
    """When the build tmpdir and the exports dir are on different
    filesystems, a plain rename raises EXDEV ("Invalid cross-device
    link") - the default layout, with /tmp on tmpfs and exports on a data
    volume. put_path must fall back to a copy rather than failing the
    upload (regression: it used os.replace, which propagated EXDEV)."""
    import errno
    import uuid
    from pathlib import Path

    from sheaf.config import settings
    from sheaf.services import export_storage

    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    monkeypatch.setattr(settings, "sheaf_data_dir", tmp_path)

    src = tmp_path / "build" / "sheaf-export-xyz.zip"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"zip-bytes")

    def _exdev(*_args, **_kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    # Simulate a genuine cross-device move: both rename syscalls fail.
    # shutil.move (new) tries os.rename, catches EXDEV, and copies instead;
    # os.replace (the old code) would just propagate it and fail the upload.
    # Patching both means this fails on the old code AND exercises the new
    # copy fallback (src/dest are actually same-device in the test).
    monkeypatch.setattr(os, "rename", _exdev)
    monkeypatch.setattr(os, "replace", _exdev)

    user_id, job_id = uuid.uuid4(), uuid.uuid4()
    location = asyncio.run(export_storage.put_path(user_id, job_id, str(src)))

    assert Path(location).read_bytes() == b"zip-bytes"
    assert not src.exists(), "tempfile should be consumed by the move"
