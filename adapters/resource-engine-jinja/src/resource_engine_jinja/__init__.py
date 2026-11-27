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
import markupsafe
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
        translations: Any = None,
    ) -> None:
        if (directory is None) == (package is None):
            raise ValueError("Pass either directory or package")
        loader: jinja2.BaseLoader = (
            jinja2.FileSystemLoader(str(directory))
            if directory is not None
            else jinja2.PackageLoader(package, folder)  # type: ignore[arg-type]
        )
        kind = SandboxedEnvironment if sandboxed else jinja2.Environment
        self.translations = translations
        self.environment = kind(
            loader=loader,
            extensions=["jinja2.ext.i18n"],
            autoescape=jinja2.select_autoescape([e.lstrip(".") for e in HTML_EXTENSIONS]),
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters.update(filters or {})
        self.environment.globals.update(globals or {})
        self.default_locale = default_locale
        self._install(None)

    def _install(self, locale: str | None, environment: Any = None) -> None:
        """``_()``, ``gettext`` and ``{% trans %}`` in ``locale``, from the
        ``Translations`` (texts as written when there are none)."""
        tr = self.translations
        locale = locale or self.default_locale
        (environment or self.environment).install_gettext_callables(  # type: ignore[attr-defined]
            gettext=(lambda m: tr.gettext(m, locale)) if tr else (lambda m: m),
            ngettext=(lambda s, p, n: tr.ngettext(s, p, n, locale))
            if tr
            else (lambda s, p, n: s if n == 1 else p),
            pgettext=(lambda c, m: tr.pgettext(c, m, locale)) if tr else (lambda c, m: m),
            newstyle=True,
        )

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
        self._install(locale)  # rendering is synchronous: no other render runs in between
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

    def variants(self, name: str) -> list[str | None]:
        """Locale folders that have ``name``, plus the catalogue's locales when
        the unlocalised template is translated with ``{% trans %}``."""
        found = [
            loc
            for loc, rest in map(split_locale, self.environment.list_templates())
            if rest == name
        ]
        if None in found and self.translations is not None and self._translatable(name):
            # translated by the catalogue: one variant per locale, the source
            # text being the default locale's
            found.remove(None)
            for locale in [self.default_locale, *self.translations.locales]:
                if locale and locale not in found:
                    found.append(locale)
        return sorted(found, key=lambda loc: (loc is not None, loc or ""))

    def _translatable(self, name: str) -> bool:
        source = self.environment.loader.get_source(self.environment, name)[0]  # type: ignore[union-attr]
        return any(mark in source for mark in ("{% trans", "_(", "gettext("))

    def preview(self, name: str, *, locale: str | None = None) -> str:
        """The template with every variable shown as ``{{ name }}`` (highlighted
        in HTML), loops over missing lists empty and conditions on them false."""
        preview = self.environment.overlay(undefined=Placeholder)
        self._install(locale, preview)
        candidates = locale_candidates(locale, self.default_locale) if locale else [None]
        for candidate in candidates:
            try:
                template = preview.get_template(localised(name, candidate))
            except jinja2.TemplateNotFound:
                continue
            try:
                return template.render({"locale": locale})
            except jinja2.TemplateError as exc:
                raise self._failed(template.name or name, exc) from exc
        raise not_found(name, locale, [localised(name, c) for c in candidates])


class Placeholder(jinja2.ChainableUndefined):
    """A missing value shown as ``{{ name }}``: plain in text, highlighted in HTML."""

    def __str__(self) -> str:
        return "{{ " + (self._undefined_name or "?") + " }}"

    def __html__(self) -> str:
        return f'<mark class="placeholder">{markupsafe.escape(str(self))}</mark>'

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(())

    def __bool__(self) -> bool:
        return False


__all__ = ["JinjaTemplates"]
