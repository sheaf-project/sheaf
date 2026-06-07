"""Storage abstraction for async data-export artefacts.

Distinct from the main `sheaf.storage` (which serves images): exports
get their own dedicated bucket when running on S3 so they can have an
S3 lifecycle expiry rule applied AND so they bypass any CDN fronting
on the image bucket. CDN proxying decrypted personal data through TLS
termination is exactly what we don't want.

Filesystem mode: exports live at /app/data/exports/{user_id}/{job_id}.zip
and are served by an authenticated streaming download endpoint. No
bucket concerns; the cleanup worker handles pruning.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from functools import partial
from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse, Response

from sheaf.config import settings
from sheaf.observability.metrics import observe_s3

_FILESYSTEM_ROOT = Path("/app/data/exports")


def _is_s3() -> bool:
    return settings.storage_backend == "s3"


def _bucket() -> str:
    return settings.s3_export_bucket or settings.s3_bucket


def _endpoint() -> str:
    return settings.s3_export_endpoint or settings.s3_endpoint


def _presign_endpoint() -> str:
    return (
        settings.s3_export_presign_endpoint
        or settings.s3_export_endpoint
        or settings.s3_presign_endpoint
        or settings.s3_endpoint
    )


def _client(endpoint: str):
    """Build an S3 client honouring whichever endpoint the caller wants
    (build vs presign — they may differ when MinIO is behind Docker).

    boto3 is an optional install (`sheaf[s3]`) so we lazy-import — pure
    filesystem deployments don't need it.
    """
    import boto3
    from botocore.config import Config

    kwargs: dict = {
        "region_name": settings.s3_region,
        # SSE-KMS (including a bucket-default KMS encryption policy)
        # requires SigV4. Presigned GETs fall back to SigV2 otherwise and
        # S3 rejects them with "requests specifying Server Side Encryption
        # with AWS KMS managed keys require AWS Signature Version 4". Pin
        # it so both API calls and presigned URLs are SigV4.
        "config": Config(signature_version="s3v4"),
    }
    if settings.s3_access_key and settings.s3_secret_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key
        kwargs["aws_secret_access_key"] = settings.s3_secret_key
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **kwargs)


def _key(user_id: uuid.UUID, job_id: uuid.UUID) -> str:
    """S3 object key. Prefix groups all exports together so a bucket
    lifecycle rule can target `exports/` if the bucket is shared with
    other content."""
    return f"exports/{user_id}/{job_id}.zip"


def _filesystem_path(user_id: uuid.UUID, job_id: uuid.UUID) -> Path:
    return _FILESYSTEM_ROOT / str(user_id) / f"{job_id}.zip"


async def _run(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(fn, *args, **kwargs))


async def put(user_id: uuid.UUID, job_id: uuid.UUID, data: bytes) -> str:
    """Persist a built export and return the file_location to store on
    the ExportJob row. Format is backend-specific: an S3 key for S3, an
    absolute filesystem path otherwise.

    Retained for the small / image-less export path where holding the
    bytes in RAM is fine. The big build path (`include_images=True`)
    uses `put_path` instead so the zip never has to live in memory.
    """
    if _is_s3():
        key = _key(user_id, job_id)
        client = _client(_endpoint())
        # Don't request SSE per-object: MinIO rejects SSE PUTs unless KMS
        # is configured (AWS S3 silently uses the bucket default), and the
        # right mechanism for "encrypt everything in this bucket at rest"
        # is the bucket's default encryption policy — operator config, set
        # once, applies to every PUT. Documented in SELFHOSTING.md.
        await observe_s3(
            "put",
            _run(
                client.put_object,
                Bucket=_bucket(),
                Key=key,
                Body=data,
                ContentType="application/zip",
            ),
        )
        return key
    path = _filesystem_path(user_id, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    await _run(path.write_bytes, data)
    return str(path)


async def put_path(
    user_id: uuid.UUID, job_id: uuid.UUID, source_path: str
) -> str:
    """Persist a built export from a filesystem path.

    Avoids the bytes-in-RAM hop for big exports. On S3 we call
    `upload_file` which transparently switches to multipart upload
    once the file crosses ~8MB; on filesystem we rename the temp file
    into place (same partition assumption — `tempfile` honours
    `tmpdir` for cross-device safety).
    """
    if _is_s3():
        key = _key(user_id, job_id)
        client = _client(_endpoint())
        await observe_s3(
            "put",
            _run(
                client.upload_file,
                source_path,
                _bucket(),
                key,
                ExtraArgs={"ContentType": "application/zip"},
            ),
        )
        return key
    dest = _filesystem_path(user_id, job_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    await _run(os.replace, source_path, str(dest))
    return str(dest)


async def delete(file_location: str) -> None:
    """Best-effort delete; ignore "already gone" errors so retries on a
    half-cleaned-up job don't fail the cleanup sweep."""
    if _is_s3():
        from botocore.exceptions import ClientError

        client = _client(_endpoint())
        try:
            await observe_s3(
                "delete",
                _run(client.delete_object, Bucket=_bucket(), Key=file_location),
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                return
            raise
        return
    try:
        os.unlink(file_location)
    except FileNotFoundError:
        return


async def download_response(file_location: str, filename: str) -> Response:
    """Return a FastAPI response that delivers the export to the caller.

    S3: 302 redirect to a short-lived presigned URL. Caller is already
    auth-checked; the presign expiry only needs to be long enough to
    survive the redirect + the user's download.
    Filesystem: stream the file via FileResponse.
    """
    if _is_s3():
        client = _client(_presign_endpoint())
        url = await observe_s3(
            "presign",
            _run(
                client.generate_presigned_url,
                "get_object",
                Params={
                    "Bucket": _bucket(),
                    "Key": file_location,
                    "ResponseContentDisposition": f'attachment; filename="{filename}"',
                },
                ExpiresIn=300,  # 5 min — enough for the redirect + download
            ),
        )
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    if not os.path.exists(file_location):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Export file no longer available",
        )
    return FileResponse(
        path=file_location,
        media_type="application/zip",
        filename=filename,
    )
