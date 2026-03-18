import asyncio
from functools import partial

import boto3
from botocore.exceptions import ClientError

from sheaf.config import settings
from sheaf.storage.base import StorageBackend


class S3Storage(StorageBackend):
    def __init__(self) -> None:
        kwargs: dict = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key,
            "region_name": settings.s3_region,
        }
        if settings.s3_endpoint:
            kwargs["endpoint_url"] = settings.s3_endpoint
        self.client = boto3.client("s3", **kwargs)
        self.bucket = settings.s3_bucket

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        await self._run(
            self.client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        if settings.s3_public_url:
            return f"{settings.s3_public_url}/{key}"
        if settings.s3_endpoint:
            return f"{settings.s3_endpoint}/{self.bucket}/{key}"
        return f"https://{self.bucket}.s3.{settings.s3_region}.amazonaws.com/{key}"

    async def get(self, key: str) -> bytes | None:
        try:
            resp = await self._run(
                self.client.get_object, Bucket=self.bucket, Key=key
            )
            return resp["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    async def delete(self, key: str) -> None:
        await self._run(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def exists(self, key: str) -> bool:
        try:
            await self._run(self.client.head_object, Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False
