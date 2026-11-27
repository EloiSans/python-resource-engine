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
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from resource_engine.errors import ConfigurationError, TemplateNotFound

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


__all__ = [
    "CONTENT_TYPES",
    "DocumentGenerator",
    "GeneratedDocument",
    "PdfConverter",
    "record_context",
]
