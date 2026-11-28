"""Documents generated from templates: an invoice, a delivery note, a
certificate, rendered per locale and attached to a record's document slot.

```python
generator = DocumentGenerator(templates, pdf=GotenbergPdfConverter(http, "http://gotenberg:3000"))


class PrintInvoice(Action):
    async def execute(self, ctx: OperationContext) -> None:
        await generator.attach(ctx, "printed", "invoice", format="pdf", locale="ca")
```

- **Templates** come from the ``TemplateRenderer`` (``JinjaTemplates``) under
  ``documents/``: ``documents/invoice.html`` for HTML and PDF,
  ``documents/invoice.md`` or ``.txt`` for text. They get ``record`` (the
  entity's fields), ``resource`` and whatever ``context`` adds, in ``locale``.
- **PDF** goes through the ``PdfConverter`` port: the HTML template, printed
  by ``GotenbergPdfConverter`` (``resource-engine-http``, headless Chromium).
- **Attaching** stores the result in the record's slot inside the operation,
  like any upload: it is part of the change and undone if it rolls back.
- **Printouts** render a record on request without storing anything:
  ``Resource.printouts = {"invoice": Printout(formats=("pdf", "html"))}`` and
  ``ResourceManager(generator=...)`` serve ``GET /invoices/7/printouts/invoice``.
  The record is read as the caller (``get`` permission, row filter, tenant),
  and fields they may not read are left out of the template's ``record``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from resource_engine.errors import ConfigurationError, OperationNotEnabled, TemplateNotFound

type DocumentFormat = Literal["pdf", "html", "md", "txt"]

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}


class PdfConverter(Protocol):
    """Prints HTML to PDF (``GotenbergPdfConverter``)."""

    async def html_to_pdf(self, html: str) -> bytes: ...


@dataclass(frozen=True)
class GeneratedDocument:
    content: bytes
    content_type: str
    extension: str


def record_context(entity: Any) -> dict[str, Any]:
    """An entity's fields for a template."""
    if dataclasses.is_dataclass(entity) and not isinstance(entity, type):
        return {f.name: getattr(entity, f.name) for f in dataclasses.fields(entity)}
    if hasattr(entity, "model_dump"):
        return entity.model_dump()
    return {k: v for k, v in vars(entity).items() if not k.startswith("_")}


@dataclass(frozen=True)
class Printout:
    """A document rendered from one record on request (``Resource.printouts``).

    Args:
        template: Template name under ``documents/`` (default: the printout's name).
        formats: Formats it can be asked for; the first is the default.
        context: Extra template values from the record's readable fields (a
            dict) and the caller: ``lambda record, actor: {"copy": True}``.
        description: For the API documents.
    """

    template: str | None = None
    formats: Sequence[DocumentFormat] = ("pdf",)
    context: Callable[[Any, Any], Mapping[str, Any]] | None = None
    description: str | None = None


def check_printouts(registry: Any, generator: Any) -> None:
    """Every printout needs a generator and known formats."""
    for definition in registry:
        printouts = definition.resource_cls.printouts
        if printouts and generator is None:
            raise ConfigurationError(
                f"Resource {definition.name!r} declares printouts but no generator is set",
                hint="Pass ResourceManager(generator=DocumentGenerator(templates, pdf=...)).",
                doc="docs/documents.md#printouts",
            )
        for name, printout in printouts.items():
            unknown = [f for f in printout.formats if f not in CONTENT_TYPES]
            if unknown or not printout.formats:
                raise ConfigurationError(
                    f"Printout {definition.name}.{name} has unknown formats {unknown}",
                    hint=f"Use some of {sorted(CONTENT_TYPES)}.",
                    doc="docs/documents.md#printouts",
                )


class DocumentGenerator:
    """Renders document templates and attaches the results to records.

    Args:
        templates: The ``TemplateRenderer``.
        pdf: The ``PdfConverter`` for ``format="pdf"``.
        prefix: Folder of the document templates.
    """

    def __init__(
        self, templates: Any, *, pdf: PdfConverter | None = None, prefix: str = "documents/"
    ) -> None:
        self.templates = templates
        self.pdf = pdf
        self.prefix = prefix

    async def generate(
        self,
        template: str,
        context: Mapping[str, Any],
        *,
        format: DocumentFormat = "pdf",
        locale: str | None = None,
    ) -> GeneratedDocument:
        if format not in CONTENT_TYPES:
            raise ConfigurationError(f"Unknown document format {format!r}")
        source = "html" if format == "pdf" else format
        name = f"{self.prefix}{template}.{source}"
        if not self.templates.exists(name, locale=locale):
            raise TemplateNotFound(
                f"No document template {name!r}",
                details={"name": name, "locale": locale, "tried": [name]},
                doc="docs/documents.md#generated-documents",
            )
        text = self.templates.render(name, dict(context), locale=locale)
        if format == "pdf":
            if self.pdf is None:
                raise ConfigurationError(
                    "PDF documents need a PdfConverter",
                    hint="DocumentGenerator(templates, pdf=GotenbergPdfConverter(...)).",
                )
            return GeneratedDocument(await self.pdf.html_to_pdf(text), CONTENT_TYPES["pdf"], "pdf")
        return GeneratedDocument(text.encode("utf-8"), CONTENT_TYPES[format], format)

    async def attach(
        self,
        ctx: Any,
        slot: str,
        template: str,
        *,
        format: DocumentFormat = "pdf",
        filename: str | None = None,
        context: Mapping[str, Any] | None = None,
        locale: str | None = None,
    ) -> Any:
        """Render ``template`` for the operation's record and attach it to
        ``slot`` (inside the operation: a rollback removes it)."""
        from resource_engine.documents.model import NewDocument

        values = {
            "record": record_context(ctx.entity),
            "resource": ctx.resource.name,
            **dict(context or {}),
        }
        document = await self.generate(template, values, format=format, locale=locale)
        name = filename or f"{template}-{getattr(ctx.entity, 'id', 'record')}.{document.extension}"
        return await ctx.documents.attach(
            NewDocument(slot, document.content, name, document.content_type.split(";")[0])
        )

    async def printout(
        self,
        manager: Any,
        resource: str,
        entity_id: Any,
        name: str,
        *,
        format: DocumentFormat | None = None,
        locale: str | None = None,
        actor: Any = None,
        tenant: str | None = None,
    ) -> tuple[GeneratedDocument, str]:
        """Render printout ``name`` of one record as ``actor``: ``(document,
        file name)``. Nothing is stored."""
        from pydantic import BaseModel

        from resource_engine.authz.fields import hidden_fields

        definition = manager.registry.get(resource)
        printout = definition.resource_cls.printouts.get(name)
        if printout is None:
            raise OperationNotEnabled(
                f"{resource} has no printout {name!r}",
                details={"printouts": sorted(definition.resource_cls.printouts)},
            )
        chosen = format or printout.formats[0]
        if chosen not in printout.formats:
            raise OperationNotEnabled(
                f"Printout {resource}.{name} is not available as {chosen}",
                details={"formats": list(printout.formats)},
            )
        record = await manager.get(resource, entity_id, actor=actor, tenant=tenant)
        values = (
            record.model_dump(exclude=set(hidden_fields(record)))
            if isinstance(record, BaseModel)
            else record_context(record)
        )
        context = {"record": values, "resource": resource}
        if printout.context is not None:
            context.update(printout.context(values, actor))
        document = await self.generate(
            printout.template or name, context, format=chosen, locale=locale
        )
        return document, f"{name}-{entity_id}.{document.extension}"


__all__ = [
    "CONTENT_TYPES",
    "DocumentGenerator",
    "GeneratedDocument",
    "PdfConverter",
    "Printout",
    "check_printouts",
    "record_context",
]
