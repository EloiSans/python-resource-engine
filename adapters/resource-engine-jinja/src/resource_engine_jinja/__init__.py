"""Jinja2 templates for Resource Engine: a ``TemplateRenderer`` for emails and
generated documents.

```python
from resource_engine_jinja import JinjaTemplates

templates = JinjaTemplates(package="invoices", folder="templates", default_locale="ca")
templates.render("email/invoice_approved.html", {"number": 7}, locale="ca-ES")
```

- **Locales:** ``ca-ES/email/x.html``, then ``ca/email/x.html``, then the
  default locale's, then ``email/x.html``.
- **Safety:** a sandboxed environment (templates can't reach Python
  internals), autoescaping for ``.html``/``.xml``, and undefined variables are
  errors (``TemplateFailed``, RE-E011), never empty strings.
- **Inheritance:** ``{% extends "email/base.html" %}`` and ``{% include %}``
  resolve from the template folder; a base layout per locale works the same
  way (``{% extends locale_path("email/base.html") %}`` is not needed: write
  the localised name).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import jinja2
from jinja2.sandbox import SandboxedEnvironment
from resource_engine.errors import TemplateFailed
from resource_engine.templates import (
    HTML_EXTENSIONS,
    locale_candidates,
    localised,
    not_found,
    split_locale,
)


class JinjaTemplates:
    """A ``TemplateRenderer`` over a folder or a package's folder.

    Args:
        directory: Templates folder on disk.
        package / folder: Or the folder inside an installed package
            (shipped with the wheel).
        default_locale: Tried after the requested locale.
        filters / globals: Extra Jinja filters and global values.
        sandboxed: ``False`` gives a plain environment, only for templates
            written by developers and never edited by users.
    """

    def __init__(
        self,
        directory: str | Path | None = None,
        *,
        package: str | None = None,
        folder: str = "templates",
        default_locale: str | None = None,
        filters: Mapping[str, Callable[..., Any]] | None = None,
        globals: Mapping[str, Any] | None = None,
        sandboxed: bool = True,
    ) -> None:
        if (directory is None) == (package is None):
            raise ValueError("Pass either directory or package")
        loader: jinja2.BaseLoader = (
            jinja2.FileSystemLoader(str(directory))
            if directory is not None
            else jinja2.PackageLoader(package, folder)  # type: ignore[arg-type]
        )
        kind = SandboxedEnvironment if sandboxed else jinja2.Environment
        self.environment = kind(
            loader=loader,
            autoescape=jinja2.select_autoescape([e.lstrip(".") for e in HTML_EXTENSIONS]),
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters.update(filters or {})
        self.environment.globals.update(globals or {})
        self.default_locale = default_locale

    def _find(self, name: str, locale: str | None) -> jinja2.Template:
        tried = []
        for candidate in locale_candidates(locale, self.default_locale):
            key = localised(name, candidate)
            tried.append(key)
            try:
                return self.environment.get_template(key)
            except jinja2.TemplateNotFound as exc:
                if exc.name != key:  # an {% include %} inside it is missing
                    raise self._failed(key, exc) from exc
                continue
            except jinja2.TemplateSyntaxError as exc:
                raise self._failed(key, exc) from exc
        raise not_found(name, locale, tried)

    @staticmethod
    def _failed(name: str, exc: Exception) -> TemplateFailed:
        return TemplateFailed(
            f"Template {name!r} failed: {exc}",
            details={"name": name, "error": f"{type(exc).__name__}: {exc}"},
            doc="docs/email.md#templates",
        )

    def render(self, name: str, context: Mapping[str, Any], *, locale: str | None = None) -> str:
        template = self._find(name, locale)
        try:
            return template.render({**context, "locale": locale or self.default_locale})
        except jinja2.TemplateError as exc:
            raise self._failed(template.name or name, exc) from exc

    def exists(self, name: str, *, locale: str | None = None) -> bool:
        from resource_engine.errors import TemplateNotFound

        try:
            self._find(name, locale)
        except TemplateNotFound:
            return False
        return True

    def names(self) -> list[str]:
        return sorted({split_locale(n)[1] for n in self.environment.list_templates()})


__all__ = ["JinjaTemplates"]
