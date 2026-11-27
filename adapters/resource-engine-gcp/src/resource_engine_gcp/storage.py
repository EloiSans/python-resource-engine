"""Google Cloud Storage as the documents' ``BlobStore`` (extra ``[storage]``),
over the JSON API with ``httpx``.

```python
from resource_engine_gcp import GcsBlobStore, gcs_token

store = GcsBlobStore(http, "my-bucket", token=gcs_token())   # Application Default Credentials
```

- **Uploads** use a resumable session: the content goes in 256 KiB-aligned
  chunks, and past ``max_bytes`` or on an error the session is cancelled,
  so no object is created.
- **Presigned URLs** are V4 signed URLs (``signer=``: credentials that can
  sign, such as a service account key); without one the store has none.
- The content's sha256 is stored as object metadata.
"""

from __future__ import annotations

import binascii
import contextlib
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from resource_engine.documents.content import Measured
from resource_engine.documents.model import content_disposition
from resource_engine.errors import BlobNotFound, StorageFailed
from resource_engine.ports.storage import BlobInfo, Content, PresignedRequest

CHUNK = 256 * 1024  # resumable uploads take multiples of 256 KiB

type TokenProvider = Callable[[], Awaitable[str | None]]


def gcs_token(scopes: Sequence[str] = ("https://www.googleapis.com/auth/devstorage.read_write",)):  # type: ignore[no-untyped-def]
    """Bearer tokens from Application Default Credentials (refreshed when due)."""
    import asyncio

    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(scopes=list(scopes))

    async def token() -> str | None:
        if not credentials.valid:
            request = google.auth.transport.requests.Request()
            await asyncio.to_thread(credentials.refresh, request)
        return credentials.token

    return token


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class GcsBlobStore:
    """``BlobStore`` (and ``SupportsPresignedUrls`` with a ``signer``) over GCS.

    Args:
        client: Caller-owned ``httpx.AsyncClient``.
        bucket: Existing bucket.
        prefix: Prepended to every key.
        token: Async function returning a bearer token (``gcs_token()``);
            ``None`` for an emulator.
        url: The API (``https://storage.googleapis.com``, or the emulator's).
        chunk_size: Upload chunk size (a multiple of 256 KiB).
        signer: ``google.auth`` credentials that can sign (service account),
            for V4 signed URLs.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        bucket: str,
        *,
        prefix: str = "",
        token: TokenProvider | None = None,
        url: str = "https://storage.googleapis.com",
        chunk_size: int = 8 * 1024 * 1024,
        signer: Any = None,
    ) -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix
        self.token = token
        self.url = url.rstrip("/")
        self.chunk_size = max(CHUNK, chunk_size - chunk_size % CHUNK)
        self.signer = signer

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def _object(self, key: str) -> str:
        return f"{self.url}/storage/v1/b/{self.bucket}/o/{quote(self._key(key), safe='')}"

    async def _headers(self) -> dict[str, str]:
        token = await self.token() if self.token else None
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _failed(self, operation: str, exc: Exception | str) -> StorageFailed:
        return StorageFailed(
            f"GCS {operation} on bucket {self.bucket!r} failed: {exc}",
            hint="Check the credentials and that the bucket exists.",
            details={"operation": operation, "bucket": self.bucket},
        )

    async def _request(self, method: str, url: str, operation: str, **kw: Any) -> httpx.Response:
        headers = {**(await self._headers()), **kw.pop("headers", {})}
        try:
            return await self.client.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise self._failed(operation, exc) from exc

    async def put(
        self, key: str, content: Content, *, content_type: str, max_bytes: int | None = None
    ) -> BlobInfo:
        measured = Measured(content, max_bytes=max_bytes)
        start = await self._request(
            "POST",
            f"{self.url}/upload/storage/v1/b/{self.bucket}/o?uploadType=resumable",
            "put",
            json={"name": self._key(key), "contentType": content_type},
            headers={"X-Upload-Content-Type": content_type},
        )
        if start.status_code >= 400 or "location" not in start.headers:
            raise self._failed("put", f"HTTP {start.status_code} {start.text[:200]}")
        session = start.headers["location"]
        buffer = bytearray()
        sent = 0
        result: httpx.Response | None = None
        try:
            async for chunk in measured:
                buffer += chunk
                while len(buffer) > self.chunk_size:  # keep the last bytes for the final call
                    part = bytes(buffer[: self.chunk_size])
                    buffer = buffer[self.chunk_size :]
                    end = sent + len(part) - 1
                    response = await self._request(
                        "PUT",
                        session,
                        "put",
                        content=part,
                        headers={"Content-Range": f"bytes {sent}-{end}/*"},
                    )
                    if response.status_code not in (200, 201, 308):
                        raise self._failed("put", f"HTTP {response.status_code}")
                    sent += len(part)
            total = sent + len(buffer)
            span = f"bytes {sent}-{total - 1}/{total}" if buffer else f"bytes */{total}"
            result = await self._request(
                "PUT", session, "put", content=bytes(buffer), headers={"Content-Range": span}
            )
            if result.status_code not in (200, 201):
                raise self._failed("put", f"HTTP {result.status_code} {result.text[:200]}")
        except BaseException:
            if result is None:  # cancel the session: no object is created
                with contextlib.suppress(httpx.HTTPError):
                    await self.client.delete(session, headers=await self._headers())
            raise
        await self._request(
            "PATCH",
            self._object(key),
            "put",
            json={"metadata": {"sha256": measured.sha256}},
        )
        data = result.json()
        return BlobInfo(
            key=key,
            size=measured.size,
            content_type=content_type,
            etag=data.get("etag"),
            last_modified=_time(data.get("updated")) or datetime.now(UTC),
            sha256=measured.sha256,
        )

    async def open(self, key: str) -> AsyncIterator[bytes]:
        headers = await self._headers()
        try:
            async with self.client.stream(
                "GET", self._object(key) + "?alt=media", headers=headers
            ) as response:
                if response.status_code == 404:
                    raise BlobNotFound(f"No blob {key!r} in bucket {self.bucket!r}")
                if response.status_code >= 400:
                    raise self._failed("get", f"HTTP {response.status_code}")
                async for chunk in response.aiter_bytes(256 * 1024):
                    yield chunk
        except httpx.HTTPError as exc:
            raise self._failed("get", exc) from exc

    async def head(self, key: str) -> BlobInfo | None:
        response = await self._request("GET", self._object(key), "head")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise self._failed("head", f"HTTP {response.status_code}")
        return self._info(response.json())

    def _info(self, data: dict[str, Any]) -> BlobInfo:
        return BlobInfo(
            key=data["name"][len(self.prefix) :],
            size=int(data.get("size", 0)),
            content_type=data.get("contentType"),
            etag=data.get("etag"),
            last_modified=_time(data.get("updated")),
            sha256=(data.get("metadata") or {}).get("sha256"),
        )

    async def delete(self, keys: Sequence[str]) -> None:
        for key in keys:
            response = await self._request("DELETE", self._object(key), "delete")
            if response.status_code >= 400 and response.status_code != 404:
                raise self._failed("delete", f"HTTP {response.status_code}")

    async def list(self, prefix: str = "") -> AsyncIterator[BlobInfo]:
        token: str | None = None
        while True:
            params = {"prefix": self._key(prefix)}
            if token:
                params["pageToken"] = token
            response = await self._request(
                "GET", f"{self.url}/storage/v1/b/{self.bucket}/o", "list", params=params
            )
            if response.status_code >= 400:
                raise self._failed("list", f"HTTP {response.status_code}")
            data = response.json()
            for item in data.get("items", []):
                yield self._info(item)
            token = data.get("nextPageToken")
            if not token:
                return

    # ------------------------------------------------------------ presigned

    def _signed(
        self,
        key: str,
        method: str,
        expires: timedelta,
        headers: dict[str, str],
        query: dict[str, str],
    ) -> str:
        if self.signer is None:
            raise StorageFailed(
                "Signed URLs need credentials that can sign",
                hint="GcsBlobStore(..., signer=service_account_credentials).",
            )
        now = datetime.now(UTC)
        stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        scope = f"{day}/auto/storage/goog4_request"
        host = "storage.googleapis.com"
        path = f"/{self.bucket}/{quote(self._key(key), safe='/')}"
        signed_headers = {"host": host, **{k.lower(): v for k, v in headers.items()}}
        names = ";".join(sorted(signed_headers))
        params = {
            "X-Goog-Algorithm": "GOOG4-RSA-SHA256",
            "X-Goog-Credential": f"{self.signer.signer_email}/{scope}",
            "X-Goog-Date": stamp,
            "X-Goog-Expires": str(int(expires.total_seconds())),
            "X-Goog-SignedHeaders": names,
            **query,
        }
        canonical_query = "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(params.items())
        )
        canonical_headers = "".join(f"{k}:{signed_headers[k]}\n" for k in sorted(signed_headers))
        request = "\n".join(
            [method, path, canonical_query, canonical_headers, names, "UNSIGNED-PAYLOAD"]
        )
        to_sign = "\n".join(
            ["GOOG4-RSA-SHA256", stamp, scope, hashlib.sha256(request.encode()).hexdigest()]
        )
        signature = binascii.hexlify(self.signer.sign_bytes(to_sign.encode())).decode()
        return f"https://{host}{path}?{canonical_query}&X-Goog-Signature={signature}"

    async def presign_put(
        self, key: str, *, content_type: str, expires: timedelta
    ) -> PresignedRequest:
        headers = {"Content-Type": content_type}
        url = self._signed(key, "PUT", expires, headers, {})
        return PresignedRequest(url, "PUT", datetime.now(UTC) + expires, headers)

    async def presign_get(
        self, key: str, *, expires: timedelta, download_name: str | None = None
    ) -> PresignedRequest:
        query = (
            {"response-content-disposition": content_disposition(download_name)}
            if download_name
            else {}
        )
        url = self._signed(key, "GET", expires, {}, query)
        return PresignedRequest(url, "GET", datetime.now(UTC) + expires)


__all__ = ["GcsBlobStore", "gcs_token"]
