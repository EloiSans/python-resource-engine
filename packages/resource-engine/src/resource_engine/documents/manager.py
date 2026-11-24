"""Document operations: attach, presigned uploads, detach, download.

They run the owner resource's lifecycle (see docs/lifecycle.md) with the
operations ``attach`` and ``detach``:

3. validation: slot, content type, declared size (``before/after_validate``)
4-5. open the unit of work, load the owner entity
6. ``before_attach[:slot]`` / ``before_detach[:slot]``
7. store the blob (a *staged effect*, deleted again on rollback) and write the
   ``Document`` row in the same unit of work; replaced documents are discarded
8. ``after_attach[:slot]`` / ``after_detach[:slot]``
9. commit (the document becomes visible)
10. purge discarded blobs (``on_commit``), ``after_commit`` hooks, events
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from pydantic import BaseModel, Field

from resource_engine.application.hooks import HookPoint
from resource_engine.application.query import ListQuery
from resource_engine.application.slots import DocumentSlot
from resource_engine.documents.events import (
    DocumentAttached,
    DocumentDetached,
    DocumentProcessed,
    DocumentRejected,
)
from resource_engine.documents.model import Document, DocumentRead, NewDocument, safe_filename
from resource_engine.errors import (
    ConfigurationError,
    ContentTypeNotAllowed,
    DocumentNotAvailable,
    DocumentSlotNotFound,
    DocumentTooLarge,
    EntityNotFound,
    OperationNotEnabled,
    RequiredDocumentMissing,
    UploadNotVerified,
)
from resource_engine.ports.storage import (
    BlobStore,
    Content,
    PresignedRequest,
    SupportsPresignedUrls,
)
from resource_engine.tracing import traced

if TYPE_CHECKING:
    from resource_engine.application.context import OperationContext
    from resource_engine.application.manager import ResourceManager
    from resource_engine.application.resource import ResourceDefinition

LIVE = ("pending", "processing", "available", "rejected")


class AttachInput(BaseModel):
    filename: str = Field(min_length=1, max_length=1000)
    content_type: str = Field(min_length=3, max_length=255)
    size: int | None = Field(default=None, ge=0)


@dataclass(frozen=True)
class UploadTicket:
    """A pending document and the request the client must make to upload it."""

    document: DocumentRead
    upload: PresignedRequest


def _now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    """Some databases (SQLite) return naive datetimes; they are stored as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _actor_label(actor: Any) -> str | None:
    if actor is None or isinstance(actor, str):
        return actor
    for attr in ("id", "sub", "username"):
        value = getattr(actor, attr, None)
        if value is not None:
            return str(value)
    return str(actor)


class DocumentManager:
    """Available as ``ResourceManager.documents`` when a blob store is configured.

    Args:
        manager: The owning ``ResourceManager``.
        store: Where contents go.
        resource: Name of the registered ``DocumentResource``.
        upload_ttl: Validity of presigned upload URLs and of pending documents.
        download_ttl: Validity of presigned download URLs.
    """

    def __init__(
        self,
        manager: ResourceManager,
        store: BlobStore,
        *,
        resource: str = "documents",
        upload_ttl: timedelta = timedelta(minutes=15),
        download_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if resource not in manager.registry:
            raise ConfigurationError(
                f"A blob store is configured but the documents resource {resource!r} "
                "is not registered",
                hint="Call registry.register_documents() at bootstrap.",
                doc="docs/documents.md#setup",
            )
        self.manager = manager
        self.store = store
        self.resource = resource
        self.definition = manager.registry.get(resource)
        assert self.definition.machine is not None
        self.machine = self.definition.machine
        self.upload_ttl = upload_ttl
        self.download_ttl = download_ttl

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def slot(definition: ResourceDefinition, name: str) -> DocumentSlot:
        try:
            return definition.document_slots[name]
        except KeyError:
            raise DocumentSlotNotFound(
                f"Resource {definition.name!r} has no document slot {name!r}",
                hint=f"Slots: {', '.join(definition.document_slots) or '(none)'}",
            ) from None

    @staticmethod
    def _check(slot: DocumentSlot, content_type: str, size: int | None) -> None:
        if not slot.accepts(content_type):
            raise ContentTypeNotAllowed(
                f"Slot {slot.name!r} does not accept {content_type!r}",
                hint=f"Accepted: {', '.join(slot.content_types)}",
                details={"accepted": list(slot.content_types)},
            )
        if size is not None and size > slot.max_bytes:
            raise DocumentTooLarge(
                f"{size} bytes exceed the {slot.max_bytes}-byte limit of slot {slot.name!r}",
                details={"max_bytes": slot.max_bytes},
            )

    def _repo(self, ctx: OperationContext) -> Any:
        return ctx.repository(self.resource)

    @staticmethod
    def key(owner_resource: str, owner_id: Any, document_id: str) -> str:
        return f"{owner_resource}/{quote(str(owner_id), safe='')}/{document_id}"

    async def _owned(
        self, ctx: OperationContext, slot: str | None, statuses: Sequence[str] = LIVE
    ) -> list[Document]:
        filters: dict[str, Any] = {
            "owner_resource": ctx.resource.name,
            "owner_id": str(ctx.entity_id),
        }
        if slot is not None:
            filters["slot"] = slot
        page = await self._repo(ctx).list(ListQuery(filters=filters, limit=10_000))
        return [d for d in page.items if d.status in statuses]

    def _owner_context(self, doc: Document, operation: str, actor: Any) -> OperationContext:
        owner = self.manager.registry.get(doc.owner_resource)
        try:
            owner_id = owner.id_type(doc.owner_id)
        except (TypeError, ValueError):
            owner_id = doc.owner_id
        return self.manager._context(
            owner, operation, actor=actor, entity_id=owner_id, slot=doc.slot
        )

    async def _get_document(self, document_id: str) -> Document:
        async with self.manager._reading() as uow:
            doc = await uow.repository(self.resource).get(document_id)
            # a detached copy: ORMs expire instances when the unit of work closes
            doc = dataclasses.replace(doc) if doc is not None else None
        if doc is None:
            raise EntityNotFound(
                f"Document {document_id!r} does not exist",
                details={"resource": self.resource, "id": document_id},
            )
        return doc

    # ------------------------------------------- in-transaction building blocks

    async def store_in(
        self,
        ctx: OperationContext,
        slot: DocumentSlot,
        content: Content,
        *,
        filename: str,
        content_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> Document:
        """Store content and add the row inside ``ctx``'s unit of work (steps 7)."""
        doc_id = uuid.uuid4().hex
        key = self.key(ctx.resource.name, ctx.entity_id, doc_id)
        info = await self.store.put(
            key, content, content_type=content_type, max_bytes=slot.max_bytes
        )
        store = self.store
        ctx.on_rollback(lambda: store.delete([key]))
        doc = Document(
            id=doc_id,
            owner_resource=ctx.resource.name,
            owner_id=str(ctx.entity_id),
            slot=slot.name,
            filename=safe_filename(filename),
            content_type=content_type,
            storage_key=key,
            size=info.size,
            sha256=info.sha256,
            created_at=_now(),
            created_by=_actor_label(ctx.actor),
            metadata=dict(metadata or {}),
        )
        await self.machine.initialize(doc)
        await self._activate(ctx, slot, doc)
        await self._repo(ctx).add(doc)
        return doc

    async def _activate(self, ctx: OperationContext, slot: DocumentSlot, doc: Document) -> None:
        """pending → available/processing, replacing the slot's previous document."""
        if not slot.multiple:
            for old in await self._owned(ctx, slot.name):
                if old.id != doc.id and old.status != "pending":
                    await self.discard_in(ctx, old)
        await self.machine.fire(doc, "start_processing" if slot.processing else "confirm", {})
        ctx.record_event(
            DocumentAttached(
                resource=ctx.resource.name,
                entity_id=ctx.entity_id,
                document_id=doc.id,
                slot=slot.name,
                content_type=doc.content_type,
                size=doc.size,
                processing=slot.processing,
            )
        )
        ctx.extras["document"] = doc

    async def discard_in(self, ctx: OperationContext, doc: Document) -> None:
        """Mark ``doc`` deleted in ``ctx``'s unit of work; purge its blob after commit."""
        await self.machine.fire(doc, "discard", {})
        await self._repo(ctx).update(doc)
        ctx.record_event(
            DocumentDetached(
                resource=doc.owner_resource,
                entity_id=ctx.entity_id,
                document_id=doc.id,
                slot=doc.slot,
            )
        )
        ctx.on_commit(lambda: self.purge([doc.id]))

    async def purge(self, document_ids: Sequence[str]) -> int:
        """Delete the blobs of deleted documents and record ``purged_at``."""
        purged = 0
        async with self.manager.uow_factory() as uow:
            repo = uow.repository(self.resource)
            for document_id in document_ids:
                doc = await repo.get(document_id)
                if doc is None or doc.status != "deleted" or doc.purged_at is not None:
                    continue
                await self.store.delete([doc.storage_key])
                doc.purged_at = _now()
                await repo.update(doc)
                purged += 1
            await uow.commit()
        return purged

    async def check_required(self, ctx: OperationContext, event: str) -> None:
        """Before a transition: every slot that lists it needs an available document."""
        for slot in ctx.resource.document_slots.values():
            if event in slot.required_for and not await self._owned(ctx, slot.name, ("available",)):
                raise RequiredDocumentMissing(
                    f"Transition {event!r} of {ctx.resource.name} {ctx.entity_id!r} needs a "
                    f"document in slot {slot.name!r}",
                    hint=f"Attach one first (POST .../documents/{slot.name}).",
                    details={"slot": slot.name, "transition": event},
                )

    async def cascade(self, ctx: OperationContext) -> None:
        """The owner is being deleted: discard all its documents."""
        for doc in await self._owned(ctx, None):
            await self.discard_in(ctx, doc)

    # ------------------------------------------------------------- operations

    @traced("attach")
    async def attach(
        self,
        resource: Any,
        entity_id: Any,
        slot: str,
        content: Content,
        *,
        filename: str,
        content_type: str,
        size: int | None = None,
        actor: Any = None,
    ) -> DocumentRead:
        """Store a file through the API (streamed) and attach it to an entity."""
        m = self.manager
        definition = m.registry.get(resource)
        slot_def = self.slot(definition, slot)
        if not slot_def.allows("api"):
            raise OperationNotEnabled(
                f"Slot {slot!r} only accepts presigned uploads",
                hint="Use request_upload() and upload directly to the store.",
            )
        ctx = m._context(
            definition,
            "attach",
            actor=actor,
            entity_id=entity_id,
            slot=slot,
            raw_input={"filename": filename, "content_type": content_type, "size": size},
        )
        await m._gate(ctx, f"attach:{slot}")
        await m._validate(ctx, AttachInput, slot)
        self._check(slot_def, ctx.data.content_type, ctx.data.size)
        hooks = definition.dispatcher
        async with m._transaction(ctx) as uow:
            await m._load(ctx)
            await m._gate_record(ctx, f"attach:{slot}", ctx.entity, input=ctx.data)
            await hooks.run(HookPoint.BEFORE_ATTACH, ctx, slot)
            doc = await self.store_in(
                ctx,
                slot_def,
                content,
                filename=ctx.data.filename,
                content_type=ctx.data.content_type,
            )
            await hooks.run(HookPoint.AFTER_ATTACH, ctx, slot)
            ctx.result = DocumentRead.model_validate(doc)
            await m._before_commit(ctx, uow)
            await uow.commit()
        await m._after_commit(ctx, slot)
        return ctx.result

    @traced("request_upload")
    async def request_upload(
        self,
        resource: Any,
        entity_id: Any,
        slot: str,
        *,
        filename: str,
        content_type: str,
        size: int | None = None,
        actor: Any = None,
    ) -> UploadTicket:
        """Create a pending document and a presigned PUT for the client.

        ``before_attach`` hooks run now; ``after_attach`` hooks run on
        ``confirm_upload``, when the content exists.
        """
        m = self.manager
        if not isinstance(self.store, SupportsPresignedUrls):
            raise ConfigurationError(
                f"{type(self.store).__name__} cannot presign uploads",
                hint="Use a store with presigned URLs (S3BlobStore) or attach through the API.",
            )
        definition = m.registry.get(resource)
        slot_def = self.slot(definition, slot)
        if not slot_def.allows("presigned"):
            raise OperationNotEnabled(
                f"Slot {slot!r} only accepts uploads through the API",
                hint="Use attach().",
            )
        ctx = m._context(
            definition,
            "attach",
            actor=actor,
            entity_id=entity_id,
            slot=slot,
            raw_input={"filename": filename, "content_type": content_type, "size": size},
        )
        await m._gate(ctx, f"attach:{slot}")
        await m._validate(ctx, AttachInput, slot)
        self._check(slot_def, ctx.data.content_type, ctx.data.size)
        async with m._transaction(ctx) as uow:
            await m._load(ctx)
            await m._gate_record(ctx, f"attach:{slot}", ctx.entity, input=ctx.data)
            await definition.dispatcher.run(HookPoint.BEFORE_ATTACH, ctx, slot)
            doc_id = uuid.uuid4().hex
            doc = Document(
                id=doc_id,
                owner_resource=definition.name,
                owner_id=str(entity_id),
                slot=slot,
                filename=safe_filename(ctx.data.filename),
                content_type=ctx.data.content_type,
                storage_key=self.key(definition.name, entity_id, doc_id),
                size=ctx.data.size,
                created_at=_now(),
                created_by=_actor_label(actor),
                expires_at=_now() + self.upload_ttl,
            )
            await self.machine.initialize(doc)
            await self._repo(ctx).add(doc)
            upload = await self.store.presign_put(
                doc.storage_key, content_type=doc.content_type, expires=self.upload_ttl
            )
            await uow.commit()
        return UploadTicket(DocumentRead.model_validate(doc), upload)

    @traced("confirm_upload", resource="documents")
    async def confirm_upload(self, document_id: str, *, actor: Any = None) -> DocumentRead:
        """Verify a presigned upload and make the document available (or processing)."""
        m = self.manager
        pending = await self._get_document(document_id)
        ctx = self._owner_context(pending, "attach", actor)
        slot_def = self.slot(ctx.resource, pending.slot)
        async with m._transaction(ctx) as uow:
            await m._load(ctx)
            await m._gate_record(ctx, f"attach:{slot_def.name}", ctx.entity)
            doc = await self._repo(ctx).get(document_id)
            if doc.status != "pending":
                ctx.result = DocumentRead.model_validate(doc)  # already confirmed
                return ctx.result
            if aware(doc.expires_at) and aware(doc.expires_at) < _now():
                raise UploadNotVerified(f"The upload of document {document_id!r} has expired")
            head = await self.store.head(doc.storage_key)
            if head is None:
                raise UploadNotVerified(
                    f"No content was uploaded for document {document_id!r}",
                    hint="PUT the file to the presigned URL before confirming.",
                )
            if head.size > slot_def.max_bytes:
                await self.store.delete([doc.storage_key])
                raise UploadNotVerified(
                    f"The uploaded file has {head.size} bytes; slot {slot_def.name!r} "
                    f"allows {slot_def.max_bytes}",
                    details={"size": head.size, "max_bytes": slot_def.max_bytes},
                )
            doc.size, doc.sha256, doc.expires_at = head.size, head.sha256, None
            await self._activate(ctx, slot_def, doc)
            await self._repo(ctx).update(doc)
            await ctx.resource.dispatcher.run(HookPoint.AFTER_ATTACH, ctx, slot_def.name)
            ctx.result = DocumentRead.model_validate(doc)
            await m._before_commit(ctx, uow)
            await uow.commit()
        await m._after_commit(ctx, slot_def.name)
        return ctx.result

    @traced("detach")
    async def detach(
        self, resource: Any, entity_id: Any, document_id: str, *, actor: Any = None
    ) -> None:
        m = self.manager
        definition = m.registry.get(resource)
        ctx = m._context(definition, "detach", actor=actor, entity_id=entity_id)
        async with m._transaction(ctx) as uow:
            await m._load(ctx)
            doc = await self._repo(ctx).get(document_id)
            if (
                doc is None
                or doc.owner_resource != definition.name
                or doc.owner_id != str(entity_id)
                or doc.status == "deleted"
            ):
                raise EntityNotFound(
                    f"{definition.name} {entity_id!r} has no document {document_id!r}",
                    details={"resource": self.resource, "id": document_id},
                )
            ctx.slot = doc.slot
            await m._gate_record(ctx, f"detach:{doc.slot}", ctx.entity)
            await definition.dispatcher.run(HookPoint.BEFORE_DETACH, ctx, doc.slot)
            await self.discard_in(ctx, doc)
            await definition.dispatcher.run(HookPoint.AFTER_DETACH, ctx, doc.slot)
            await m._before_commit(ctx, uow)
            await uow.commit()
        await m._after_commit(ctx, doc.slot)

    @traced("list_documents")
    async def list(
        self, resource: Any, entity_id: Any, slot: str | None = None, *, actor: Any = None
    ) -> list[DocumentRead]:
        """Documents of an entity; needs ``get`` on the owner."""
        definition = self.manager.registry.get(resource)
        if slot is not None:
            self.slot(definition, slot)
        ctx = self.manager._context(definition, "get", entity_id=entity_id, actor=actor)
        await self.manager._gate(ctx, "get")
        async with self.manager._reading(ctx):
            await self.manager._load(ctx)
            await self.manager._gate_record(ctx, "get", ctx.entity)
            docs = await self._owned(ctx, slot)
            docs.sort(key=lambda d: (aware(d.created_at) or _now(), d.id))
            return [DocumentRead.model_validate(d) for d in docs]

    async def _gate_owner(self, doc: Document, action: str, actor: Any) -> None:
        """Documents inherit visibility from their owner: ``get`` on it for metadata,
        ``download:<slot>`` for content."""
        m = self.manager
        ctx = self._owner_context(doc, "get", actor)
        if m.authorizer is None and ctx.resource.tenant_field is None:
            return
        async with m._reading(ctx) as uow:
            owner = await uow.repository(ctx.resource.name).get(ctx.entity_id)
            if owner is None:
                raise EntityNotFound(f"Document {doc.id!r} does not exist")
            try:
                await m._check_tenant(ctx, owner)
            except EntityNotFound:
                raise EntityNotFound(f"Document {doc.id!r} does not exist") from None
            await m._gate_record(ctx, action, owner)

    async def get(self, document_id: str, *, actor: Any = None) -> DocumentRead:
        doc = await self._get_document(document_id)
        await self._gate_owner(doc, "get", actor)
        return DocumentRead.model_validate(doc)

    async def _available(self, document_id: str, actor: Any = None) -> Document:
        doc = await self._get_document(document_id)
        await self._gate_owner(doc, f"download:{doc.slot}", actor)
        if doc.status != "available":
            raise DocumentNotAvailable(
                f"Document {document_id!r} is {doc.status}",
                hint="Only available documents can be downloaded.",
                details={"status": doc.status},
            )
        return doc

    async def open(
        self, document_id: str, *, actor: Any = None
    ) -> tuple[DocumentRead, AsyncIterator[bytes]]:
        """Metadata and a stream of the content of an available document."""
        doc = await self._available(document_id, actor)
        return DocumentRead.model_validate(doc), self.store.open(doc.storage_key)

    async def download_url(
        self, document_id: str, *, expires: timedelta | None = None, actor: Any = None
    ) -> PresignedRequest:
        if not isinstance(self.store, SupportsPresignedUrls):
            raise ConfigurationError(f"{type(self.store).__name__} cannot presign downloads")
        doc = await self._available(document_id, actor)
        return await self.store.presign_get(
            doc.storage_key, expires=expires or self.download_ttl, download_name=doc.filename
        )

    # -------------------------------------------------------------- processing

    async def finish_processing(
        self,
        document_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        derived: Sequence[NewDocument] = (),
        error: str | None = None,
    ) -> DocumentRead | None:
        """Close processing: ``complete`` (with metadata and derived documents) or
        ``reject``. Idempotent: documents no longer processing are left alone."""
        m = self.manager
        current = await self._get_document(document_id)
        ctx = self._owner_context(current, "process", None)
        async with m._transaction(ctx) as uow:
            repo = self._repo(ctx)
            doc = await repo.get(document_id)
            if doc.status != "processing":
                return None
            owner = await uow.repository(ctx.resource.name).get(ctx.entity_id)
            if owner is None:  # the owner disappeared meanwhile
                await self.discard_in(ctx, doc)
            elif error:
                await self.machine.fire(doc, "reject", {})
                doc.error = error
                await repo.update(doc)
                ctx.record_event(
                    DocumentRejected(
                        resource=ctx.resource.name,
                        entity_id=ctx.entity_id,
                        document_id=doc.id,
                        slot=doc.slot,
                        error=error,
                    )
                )
            else:
                ctx.entity = owner
                doc.metadata = {**(doc.metadata or {}), **(metadata or {})}
                await self.machine.fire(doc, "complete", {})
                await repo.update(doc)
                for new in derived:
                    slot_def = self.slot(ctx.resource, new.slot)
                    self._check(slot_def, new.content_type, None)
                    await self.store_in(
                        ctx,
                        slot_def,
                        new.content,
                        filename=new.filename,
                        content_type=new.content_type,
                        metadata={**new.metadata, "derived_from": doc.id},
                    )
                ctx.record_event(
                    DocumentProcessed(
                        resource=ctx.resource.name,
                        entity_id=ctx.entity_id,
                        document_id=doc.id,
                        slot=doc.slot,
                    )
                )
            await m._before_commit(ctx, uow)
            await uow.commit()
        await m._after_commit(ctx)
        return DocumentRead.model_validate(doc)


class ContextDocuments:
    """``ctx.documents``: document operations inside the current transaction.

    For actions and hooks that produce files (a rendered PDF) or remove them.
    Owner hooks (``before_attach`` ...) are not run: the caller is engine code.
    """

    def __init__(self, documents: DocumentManager, ctx: OperationContext) -> None:
        self._documents = documents
        self._ctx = ctx

    def _require_tx(self) -> None:
        if self._ctx.uow is None or self._ctx.entity_id is None:
            raise RuntimeError(
                "ctx.documents needs an active transaction on an entity "
                "(an instance action or a hook between before_* and after_*)"
            )

    async def attach(self, new: NewDocument) -> DocumentRead:
        self._require_tx()
        slot = DocumentManager.slot(self._ctx.resource, new.slot)
        DocumentManager._check(slot, new.content_type, None)
        doc = await self._documents.store_in(
            self._ctx,
            slot,
            new.content,
            filename=new.filename,
            content_type=new.content_type,
            metadata=new.metadata,
        )
        return DocumentRead.model_validate(doc)

    async def detach(self, document_id: str) -> None:
        self._require_tx()
        doc = await self._documents._repo(self._ctx).get(document_id)
        if doc is None or doc.owner_id != str(self._ctx.entity_id) or doc.status == "deleted":
            raise EntityNotFound(f"No document {document_id!r} on this entity")
        await self._documents.discard_in(self._ctx, doc)

    async def list(self, slot: str | None = None) -> list[DocumentRead]:
        self._require_tx()
        docs = await self._documents._owned(self._ctx, slot)
        return [DocumentRead.model_validate(d) for d in docs]
