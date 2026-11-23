"""Events recorded by document operations.

``resource``/``entity_id`` are the *owner's*, so the envelope key keeps all events
of an entity (its own and its documents') in order.
"""

from __future__ import annotations

from dataclasses import dataclass

from resource_engine.application.events import ResourceEvent


@dataclass(frozen=True, kw_only=True)
class DocumentEvent(ResourceEvent):
    document_id: str
    slot: str


@dataclass(frozen=True, kw_only=True)
class DocumentAttached(DocumentEvent):
    """Content is stored. ``processing`` tells the pipeline to pick it up."""

    content_type: str
    size: int | None
    processing: bool


@dataclass(frozen=True, kw_only=True)
class DocumentDetached(DocumentEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class DocumentProcessed(DocumentEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class DocumentRejected(DocumentEvent):
    error: str
