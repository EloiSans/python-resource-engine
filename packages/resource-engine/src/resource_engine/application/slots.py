"""Document slots: which files a resource accepts."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

type UploadMode = Literal["api", "presigned", "any"]

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class DocumentSlot:
    """A named place for documents on a resource (``Resource.documents``).

    Args:
        name: Slot identifier (``signed_pdf``), used in URLs and hook qualifiers.
        content_types: Accepted MIME types; ``*`` patterns allowed (``image/*``).
        max_bytes: Size limit, enforced while streaming and on upload confirmation.
        multiple: Several documents at once; otherwise attaching replaces the
            previous one.
        upload: ``api`` (bytes go through the API), ``presigned`` (direct to the
            store) or ``any``.
        required_for: Transitions that need an available document in this slot.
        processing: Documents go through the processing pipeline before they
            become available.
        description: Functional description (shown in generated docs).
    """

    name: str
    content_types: Sequence[str] = ("*/*",)
    max_bytes: int = 25 * 1024 * 1024
    multiple: bool = False
    upload: UploadMode = "any"
    required_for: Sequence[str] = ()
    processing: bool = False
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_types", tuple(t.lower() for t in self.content_types))
        object.__setattr__(self, "required_for", tuple(self.required_for))

    def accepts(self, content_type: str) -> bool:
        base = content_type.split(";", 1)[0].strip().lower()
        return any(fnmatch.fnmatchcase(base, pattern) for pattern in self.content_types)

    def allows(self, mode: Literal["api", "presigned"]) -> bool:
        return self.upload in (mode, "any")

    def problems(self) -> list[str]:
        out = []
        if not _NAME.match(self.name):
            out.append(f"slot name {self.name!r} must be snake_case")
        if self.max_bytes <= 0:
            out.append(f"slot {self.name!r}: max_bytes must be positive")
        if self.upload not in ("api", "presigned", "any"):
            out.append(f"slot {self.name!r}: upload must be 'api', 'presigned' or 'any'")
        if not self.content_types:
            out.append(f"slot {self.name!r}: content_types is empty")
        return out
