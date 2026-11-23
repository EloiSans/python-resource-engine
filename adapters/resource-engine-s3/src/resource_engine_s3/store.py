from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from resource_engine.documents.content import Measured
from resource_engine.documents.model import content_disposition
from resource_engine.errors import BlobNotFound, StorageFailed
from resource_engine.ports.storage import BlobInfo, Content, PresignedRequest

MIN_PART = 5 * 1024 * 1024  # S3 minimum for all parts but the last
DELETE_BATCH = 1000


def s3_client_options(
    endpoint_url: str | None = None, *, region: str = "us-east-1", path_style: bool = False
) -> dict[str, Any]:
    """Keyword arguments for ``session.create_client("s3", ...)``.

    ``path_style=True`` for Garage and MinIO (``http://host/bucket/key``).
    """
    from aiobotocore.config import AioConfig

    config = AioConfig(
        s3={"addressing_style": "path" if path_style else "auto"},
        signature_version="s3v4",
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    options: dict[str, Any] = {"region_name": region, "config": config}
    if endpoint_url:
        options["endpoint_url"] = endpoint_url
    return options


def _missing(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")


class S3BlobStore:
    """``BlobStore`` and ``SupportsPresignedUrls`` over an aiobotocore S3 client.

    Args:
        client: aiobotocore S3 client (owned by the caller); see ``s3_client_options``.
        bucket: Existing bucket.
        prefix: Prepended to every key (e.g. ``"documents/"``), so one bucket can
            serve several applications.
        part_size: Multipart part size for streamed uploads (min 5 MiB).

    Small contents are sent with one ``PutObject``; larger ones with a multipart
    upload, aborted on errors or when ``max_bytes`` is exceeded, so no partial
    object is left. The content's sha256 is stored as object metadata when known
    before the upload (single part).
    """

    def __init__(
        self, client: Any, bucket: str, *, prefix: str = "", part_size: int = 8 * 1024 * 1024
    ) -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix
        self.part_size = max(part_size, MIN_PART)

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def _failed(self, operation: str, exc: Exception) -> StorageFailed:
        return StorageFailed(
            f"S3 {operation} on bucket {self.bucket!r} failed: {exc}",
            hint="Check the endpoint, credentials and that the bucket exists.",
            details={"operation": operation, "bucket": self.bucket},
        )

    async def put(
        self, key: str, content: Content, *, content_type: str, max_bytes: int | None = None
    ) -> BlobInfo:
        measured = Measured(content, max_bytes=max_bytes)
        buffer = bytearray()
        upload_id: str | None = None
        parts: list[dict[str, Any]] = []
        full_key = self._key(key)
        try:
            async for chunk in measured:
                buffer += chunk
                while len(buffer) >= self.part_size:
                    if upload_id is None:
                        created = await self.client.create_multipart_upload(
                            Bucket=self.bucket, Key=full_key, ContentType=content_type
                        )
                        upload_id = created["UploadId"]
                    part, buffer = bytes(buffer[: self.part_size]), buffer[self.part_size :]
                    response = await self.client.upload_part(
                        Bucket=self.bucket,
                        Key=full_key,
                        UploadId=upload_id,
                        PartNumber=len(parts) + 1,
                        Body=part,
                    )
                    parts.append({"PartNumber": len(parts) + 1, "ETag": response["ETag"]})
            if upload_id is None:
                response = await self.client.put_object(
                    Bucket=self.bucket,
                    Key=full_key,
                    Body=bytes(buffer),
                    ContentType=content_type,
                    Metadata={"sha256": measured.sha256},
                )
                etag = response.get("ETag")
            else:
                if buffer:
                    response = await self.client.upload_part(
                        Bucket=self.bucket,
                        Key=full_key,
                        UploadId=upload_id,
                        PartNumber=len(parts) + 1,
                        Body=bytes(buffer),
                    )
                    parts.append({"PartNumber": len(parts) + 1, "ETag": response["ETag"]})
                response = await self.client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=full_key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
                etag = response.get("ETag")
        except BaseException as exc:
            if upload_id is not None:
                with contextlib.suppress(Exception):  # best effort
                    await self.client.abort_multipart_upload(
                        Bucket=self.bucket, Key=full_key, UploadId=upload_id
                    )
            if isinstance(exc, ClientError | BotoCoreError):
                raise self._failed("put", exc) from exc
            raise
        return BlobInfo(
            key=key,
            size=measured.size,
            content_type=content_type,
            etag=(etag or "").strip('"') or None,
            last_modified=datetime.now(UTC),
            sha256=measured.sha256,
        )

    async def open(self, key: str) -> AsyncIterator[bytes]:
        try:
            response = await self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except ClientError as exc:
            if _missing(exc):
                raise BlobNotFound(f"No blob {key!r} in bucket {self.bucket!r}") from exc
            raise self._failed("get", exc) from exc
        async with response["Body"] as body:
            while chunk := await body.read(256 * 1024):
                yield chunk

    async def head(self, key: str) -> BlobInfo | None:
        try:
            response = await self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except ClientError as exc:
            if _missing(exc):
                return None
            raise self._failed("head", exc) from exc
        return BlobInfo(
            key=key,
            size=response["ContentLength"],
            content_type=response.get("ContentType"),
            etag=(response.get("ETag") or "").strip('"') or None,
            last_modified=response.get("LastModified"),
            sha256=(response.get("Metadata") or {}).get("sha256"),
        )

    async def delete(self, keys: Sequence[str]) -> None:
        for start in range(0, len(keys), DELETE_BATCH):
            batch = [{"Key": self._key(k)} for k in keys[start : start + DELETE_BATCH]]
            try:
                response = await self.client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True}
                )
            except ClientError as exc:
                raise self._failed("delete", exc) from exc
            errors = [e for e in response.get("Errors", []) if e.get("Code") != "NoSuchKey"]
            if errors:
                raise self._failed("delete", RuntimeError(errors[0].get("Message")))

    async def list(self, prefix: str = "") -> AsyncIterator[BlobInfo]:
        paginator = self.client.get_paginator("list_objects_v2")
        try:
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(prefix)):
                for item in page.get("Contents", []):
                    yield BlobInfo(
                        key=item["Key"][len(self.prefix) :],
                        size=item["Size"],
                        etag=(item.get("ETag") or "").strip('"') or None,
                        last_modified=item.get("LastModified"),
                    )
        except ClientError as exc:
            raise self._failed("list", exc) from exc

    async def presign_put(
        self, key: str, *, content_type: str, expires: timedelta
    ) -> PresignedRequest:
        url = await self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": self._key(key), "ContentType": content_type},
            ExpiresIn=int(expires.total_seconds()),
        )
        return PresignedRequest(
            url, "PUT", datetime.now(UTC) + expires, {"Content-Type": content_type}
        )

    async def presign_get(
        self, key: str, *, expires: timedelta, download_name: str | None = None
    ) -> PresignedRequest:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": self._key(key)}
        if download_name:
            params["ResponseContentDisposition"] = content_disposition(download_name)
        url = await self.client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=int(expires.total_seconds())
        )
        return PresignedRequest(url, "GET", datetime.now(UTC) + expires)
