"""Processing pipeline: pluggable steps run on documents of slots with
``processing=True`` (virus scan, text extraction, thumbnails, classification...).

The pipeline is an event handler for ``DocumentAttached``. Run it with the
consumers (``MessageProcessor`` + ``MessageConsumer``, or the push endpoint), so
retries, dead-lettering and de-duplication come from the messaging layer::

    router = EventRouter(ignore_unknown=True)
    DocumentPipeline(manager.documents, [MyScanner()]).register(router)
    consumer = MessageConsumer(source, MessageProcessor(router, name="documents"))
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from resource_engine.documents.model import DocumentRead, NewDocument
from resource_engine.messaging.consumer import EventRouter
from resource_engine.messaging.envelope import EventEnvelope

type OpenContent = Callable[[], AsyncIterator[bytes]]


@dataclass(frozen=True)
class ProcessingResult:
    """What a processor found.

    Attributes:
        metadata: Stored under ``document.metadata[<processor name>]``.
        derived: New documents to attach to the same owner (e.g. a thumbnail).
        reject: Reason to refuse the document; later processors do not run.
    """

    metadata: dict[str, Any] = field(default_factory=dict)
    derived: list[NewDocument] = field(default_factory=list)
    reject: str | None = None


class DocumentProcessor(Protocol):
    """One pipeline step. Raise to retry later (transient failure); return
    ``ProcessingResult(reject=...)`` to refuse the document for good."""

    name: str

    def accepts(self, document: DocumentRead) -> bool: ...

    async def process(self, document: DocumentRead, content: OpenContent) -> ProcessingResult: ...


class DocumentPipeline:
    def __init__(self, documents: Any, processors: Sequence[DocumentProcessor]) -> None:
        self.documents = documents
        self.processors = list(processors)

    def register(self, router: EventRouter) -> EventRouter:
        router.register("DocumentAttached", self.handle)
        return router

    async def handle(self, envelope: EventEnvelope) -> None:
        if not envelope.payload.get("processing"):
            return
        document_id = envelope.payload["document_id"]
        doc = await self.documents._get_document(document_id)
        if doc.status != "processing":
            return  # already processed (redelivery) or discarded
        read = DocumentRead.model_validate(doc)
        store = self.documents.store

        def content() -> AsyncIterator[bytes]:
            return store.open(doc.storage_key)

        metadata: dict[str, Any] = {}
        derived: list[NewDocument] = []
        error: str | None = None
        for processor in self.processors:
            if not processor.accepts(read):
                continue
            result = await processor.process(read, content)
            if result.metadata:
                metadata[processor.name] = result.metadata
            derived += result.derived
            if result.reject:
                error = f"{processor.name}: {result.reject}"
                break
        await self.documents.finish_processing(
            document_id, metadata=metadata, derived=[] if error else derived, error=error
        )
