"""Template port: render named templates per locale (emails, documents).

``resource_engine.templates`` ships ``StringTemplates`` for tests and small
apps; ``resource-engine-jinja`` the Jinja2 renderer (autoescaping, sandbox).
Names are paths with an extension (``email/invoice_approved.html``); the
extension says how values are escaped.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class TemplateRenderer(Protocol):
    def render(self, name: str, context: Mapping[str, Any], *, locale: str | None = None) -> str:
        """The template for the most specific locale that has it (``ca-ES``,
        then ``ca``, then the default). Raises ``TemplateNotFound`` (RE-E120)
        when none does, ``TemplateFailed`` (RE-E121) when rendering fails."""
        ...

    def exists(self, name: str, *, locale: str | None = None) -> bool:
        """Whether ``render`` would find a template."""
        ...

    def names(self) -> list[str]:
        """Every template name, without the locale (for ``check``)."""
        ...


class PreviewableTemplates(TemplateRenderer, Protocol):
    """A renderer the documentation can show templates from (both renderers
    shipped implement it)."""

    def variants(self, name: str) -> list[str | None]:
        """The locales that have ``name`` itself (``None``: the unlocalised one)."""
        ...

    def preview(self, name: str, *, locale: str | None = None) -> str:
        """The template rendered with every variable shown as a placeholder
        (``{{ customer }}``), for the generated docs: no data is invented.

        ``locale`` falls back like ``render`` (``ca-ES``, ``ca``, the default
        locale); ``None`` is the unlocalised template only. Raises
        ``TemplateNotFound`` when there is none."""
        ...
