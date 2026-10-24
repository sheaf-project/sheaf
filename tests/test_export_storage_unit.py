"""export_storage.delete_all_for_user on the filesystem backend. Pure.

Account deletion removes a user's exports by prefix rather than by row,
so a build in flight or a row that never got its file_location is still
cleaned up. The S3 branch is the same shape over list_objects_v2 and is
exercised against a bucket by the stack suite; this pins the filesystem
layout and that a neighbour's directory is never touched.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest


def _seed(root: Path, user_id: uuid.UUID, n: int) -> Path:
    user_dir = root / "exports" / str(user_id)
    user_dir.mkdir(parents=True)
    for _ in range(n):
        (user_dir / f"{uuid.uuid4()}.zip").write_bytes(b"zip")
    return user_dir


def test_delete_all_for_user_removes_only_that_users_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sheaf.config import settings
    from sheaf.services import export_storage

    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    monkeypatch.setattr(settings, "sheaf_data_dir", tmp_path)

    gone, stays = uuid.uuid4(), uuid.uuid4()
    gone_dir = _seed(tmp_path, gone, 2)
    stays_dir = _seed(tmp_path, stays, 1)

    assert asyncio.run(export_storage.delete_all_for_user(gone)) == 2
    assert not gone_dir.exists()
    assert len(list(stays_dir.iterdir())) == 1


def test_delete_all_for_user_is_a_no_op_for_a_user_with_no_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sheaf.config import settings
    from sheaf.services import export_storage

    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    monkeypatch.setattr(settings, "sheaf_data_dir", tmp_path)

    assert asyncio.run(export_storage.delete_all_for_user(uuid.uuid4())) == 0
