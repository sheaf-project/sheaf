import abc
import logging

logger = logging.getLogger("sheaf.storage")

_backend: "StorageBackend | None" = None


class StorageBackend(abc.ABC):
    @abc.abstractmethod
    async def put(self, key: str, data: bytes, content_type: str) -> str:
        """Store a file. Returns the public URL."""

    @abc.abstractmethod
    async def get(self, key: str) -> bytes | None:
        """Retrieve a file by key. Returns None if not found."""

    @abc.abstractmethod
    async def delete(self, key: str) -> None:
        """Delete a file by key."""

    @abc.abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if a file exists."""

    @abc.abstractmethod
    async def list_keys(self, prefix: str) -> list[str]:
        """List all keys under a prefix."""

    @abc.abstractmethod
    async def size(self, key: str) -> int:
        """Return the size of a file in bytes. 0 if not found."""


def get_storage() -> "StorageBackend":
    global _backend
    if _backend is None:
        from sheaf.config import settings

        if settings.storage_backend == "s3":
            try:
                from sheaf.storage.s3 import S3Storage
            except ImportError:
                raise RuntimeError(
                    "STORAGE_BACKEND=s3 requires the 's3' extra. "
                    "Install with: pip install sheaf[s3]  "
                    "(Docker: add 's3' to the pip install extras in Dockerfile)"
                ) from None

            _backend = S3Storage()
            logger.info("Using S3 storage backend (bucket: %s)", settings.s3_bucket)
        else:
            from sheaf.storage.filesystem import FilesystemStorage

            _backend = FilesystemStorage()
            logger.info("Using filesystem storage backend (%s)", settings.storage_path)
    return _backend
