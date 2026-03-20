import aiofiles
import aiofiles.os

from sheaf.config import settings
from sheaf.storage.base import StorageBackend


class FilesystemStorage(StorageBackend):
    def __init__(self) -> None:
        self.root = settings.storage_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str):
        """Resolve a key to an absolute path, rejecting traversal attempts."""
        resolved = (self.root / key).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Path traversal detected")
        return resolved

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        path = self._safe_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(str(path), "wb") as f:
            await f.write(data)
        return f"/v1/files/{key}"

    async def get(self, key: str) -> bytes | None:
        path = self._safe_path(key)
        if not path.exists():
            return None
        async with aiofiles.open(str(path), "rb") as f:
            return await f.read()

    async def delete(self, key: str) -> None:
        path = self._safe_path(key)
        if path.exists():
            await aiofiles.os.remove(str(path))

    async def exists(self, key: str) -> bool:
        try:
            return self._safe_path(key).exists()
        except ValueError:
            return False

    async def list_keys(self, prefix: str) -> list[str]:
        base = self._safe_path(prefix)
        if not base.exists():
            return []
        keys = []
        for path in base.rglob("*"):
            if path.is_file():
                keys.append(str(path.relative_to(self.root)))
        return keys

    async def size(self, key: str) -> int:
        path = self._safe_path(key)
        if not path.exists():
            return 0
        return path.stat().st_size
