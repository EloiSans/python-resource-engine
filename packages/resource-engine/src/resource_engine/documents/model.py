"""The ``Document`` entity and its read schema."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class Document:
    """A file attached to an entity of another resource.

    The content lives in the blob store under ``storage_key``; this row is the
    source of truth: a blob without a row is garbage (see ``DocumentJanitor``).
    """

    id: str
    owner_resource: str
    owner_id: str
    slot: str
    filename: str
    content_type: str
    storage_key: str
    status: str | None = None
    size: int | None = None
    sha256: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    purged_at: datetime | None = None


class DocumentRead(BaseModel):
    """Public representation of a document."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Document identifier")
    owner_resource: str = Field(description="Resource the document belongs to")
    owner_id: str = Field(description="Identifier of the owning entity")
    slot: str = Field(description="Slot of the owning resource")
    status: str = Field(description="pending, processing, available, rejected or deleted")
    filename: str = Field(description="Original file name (sanitised)")
    content_type: str = Field(description="MIME type")
    size: int | None = Field(default=None, description="Size in bytes")
    sha256: str | None = Field(default=None, description="SHA-256 of the content, hex")
    created_at: datetime | None = Field(default=None, description="When it was attached")
    created_by: str | None = Field(default=None, description="Actor that attached it")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Data from processing")
    error: str | None = Field(default=None, description="Why processing rejected it")


@dataclass(frozen=True)
class NewDocument:
    """A document produced by code (an action, a processor) rather than uploaded."""

    slot: str
    content: Any  # bytes or AsyncIterable[bytes]
    filename: str
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


def safe_filename(name: str, *, fallback: str = "file") -> str:
    """Base name only, no control or path characters, at most 200 characters."""
    name = unicodedata.normalize("NFC", name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE.sub("_", name).strip(" .")
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 200 - len(ext) - 1] + dot + ext) if dot and len(ext) < 20 else name[:200]
    return name or fallback


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """RFC 6266 header value with an ASCII fallback and the UTF-8 ``filename*``."""
    from urllib.parse import quote

    name = safe_filename(filename)
    fallback = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
