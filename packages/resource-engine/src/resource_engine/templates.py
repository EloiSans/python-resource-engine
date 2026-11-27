"""Templates: locale fallback shared by every renderer, and ``StringTemplates``,
a renderer for tests and small apps (``string.Template`` placeholders).

For real templates (loops, conditions, inheritance, autoescaping) use
``resource_engine_jinja.JinjaTemplates``. See docs/email.md#templates.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from string import Template
from typing import Any

from resource_engine.errors import TemplateFailed, TemplateNotFound

#: Extensions whose values are HTML-escaped.
HTML_EXTENSIONS = (".html", ".htm", ".xml")


def locale_candidates(locale: str | None, default: str | None = None) -> list[str | None]:
    """Where to look, most specific first: ``ca-ES`` → ``["ca-ES", "ca", <default>, None]``.

    ``None`` is the unlocalised template. ``_`` and ``-`` are accepted alike
    (``pt_BR`` is ``pt-BR``).
    """
    out: list[str | None] = []
    for value in (locale, default):
        if not value:
            continue
        value = value.replace("_", "-")
        parts = value.split("-")
        for size in range(len(parts), 0, -1):
            candidate = "-".join(parts[:size])
            if candidate not in out:
                out.append(candidate)
    out.append(None)
    return out


#: A leading path segment read as a locale: ``ca``, ``pt-BR``, ``zh-Hant``, ``es-419``.
LOCALE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2}|-[A-Z][a-z]{3}|-\d{3})?$")


def split_locale(key: str) -> tuple[str | None, str]:
    """``ca/email/x.txt`` → ``("ca", "email/x.txt")``; ``email/x.txt`` → ``(None, ...)``.

    Top-level folders of two or three lower-case letters are locales, so
    don't name template folders that way (``pdf/``): use ``documents/``.
    """
    first, _, rest = key.partition("/")
    if rest and LOCALE.match(first):
        return first, rest
    return None, key


def localised(name: str, locale: str | None) -> str:
    """``email/x.txt`` in ``ca`` is ``ca/email/x.txt``."""
    return f"{locale}/{name}" if locale else name


def not_found(name: str, locale: str | None, tried: list[str]) -> TemplateNotFound:
    return TemplateNotFound(
        f"No template {name!r}" + (f" for locale {locale!r}" if locale else ""),
        details={"name": name, "locale": locale, "tried": tried},
        hint="Add the template, or one for a broader locale or with no locale.",
        doc="docs/email.md#templates",
    )


class StringTemplates:
    """Templates from a mapping, with ``$name`` / ``${name}`` placeholders.

    Keys are names with an optional locale prefix (``ca/email/x.txt``).
    Values in ``.html`` templates are escaped. A missing variable is
    ``TemplateFailed``, never an empty string.
    """

    def __init__(self, templates: Mapping[str, str], *, default_locale: str | None = None) -> None:
        self.templates = dict(templates)
        self.default_locale = default_locale

    def _find(self, name: str, locale: str | None) -> tuple[str, str]:
        tried = []
        for candidate in locale_candidates(locale, self.default_locale):
            key = localised(name, candidate)
            tried.append(key)
            if key in self.templates:
                return key, self.templates[key]
        raise not_found(name, locale, tried)

    def render(self, name: str, context: Mapping[str, Any], *, locale: str | None = None) -> str:
        key, source = self._find(name, locale)
        escape = name.endswith(HTML_EXTENSIONS)
        values = {k: html.escape(str(v)) if escape else v for k, v in context.items()}
        try:
            return Template(source).substitute(values)
        except (KeyError, ValueError) as exc:
            raise TemplateFailed(
                f"Template {key!r} failed: {exc!r}",
                details={"name": key, "error": repr(exc)},
            ) from exc

    def exists(self, name: str, *, locale: str | None = None) -> bool:
        try:
            self._find(name, locale)
        except TemplateNotFound:
            return False
        return True

    def names(self) -> list[str]:
        return sorted({split_locale(key)[1] for key in self.templates})


__all__ = [
    "HTML_EXTENSIONS",
    "LOCALE",
    "StringTemplates",
    "locale_candidates",
    "localised",
    "not_found",
    "split_locale",
]
