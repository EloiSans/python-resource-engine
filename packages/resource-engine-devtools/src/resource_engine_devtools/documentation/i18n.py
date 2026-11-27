"""Languages of the generated documentation.

Two catalogues meet here:

- **The generator's own texts** (headings, table columns, explanations),
  written in English and translated by catalogues shipped with devtools
  (``documentation/locale/<locale>/LC_MESSAGES/docs.po``: Catalan, Spanish).
- **The application's texts** (docstrings, descriptions, state names), written
  in the application's source language and translated by its own catalogue
  (``resource-engine i18n``): ``translate_model`` returns a copy of the model
  in another locale.

``render_markdown`` and ``render_site`` render in the locale set with
``docs_locale``; without one the texts stay as written.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from importlib.resources import files
from typing import Any

from resource_engine.templates import locale_candidates

DOMAIN = "docs"

_locale: ContextVar[str | None] = ContextVar("resource_engine_docs_locale", default=None)


@cache
def _catalogue(locale: str) -> dict[str, str]:
    """The generator's catalogue for one locale (``{}`` when there is none)."""
    from babel.messages.pofile import read_po

    path = files(__package__) / "locale" / locale / "LC_MESSAGES" / f"{DOMAIN}.po"
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        catalog = read_po(handle)
    return {
        str(m.id): str(m.string)
        for m in catalog
        if m.id and m.string and not m.fuzzy and isinstance(m.id, str)
    }


def generator_locales() -> list[str]:
    """Locales the generator's own texts are translated into (plus English)."""
    root = files(__package__) / "locale"
    found = [p.name for p in root.iterdir() if (p / "LC_MESSAGES" / f"{DOMAIN}.po").is_file()]
    return sorted({"en", *found})


@contextmanager
def docs_locale(locale: str | None) -> Iterator[None]:
    token = _locale.set(locale)
    try:
        yield
    finally:
        _locale.reset(token)


def current() -> str | None:
    return _locale.get()


def t(message: str, **values: Any) -> str:
    """A generator text in the current docs locale, with ``{name}`` values."""
    locale = _locale.get()
    text = message
    if locale:
        for candidate in locale_candidates(locale)[:-1]:
            found = _catalogue(candidate).get(message)  # type: ignore[arg-type]
            if found:
                text = found
                break
    return text.format(**values) if values else text


# ------------------------------------------------- the application's texts

#: Model fields that hold the application's own prose.
TEXT_FIELDS = {"summary", "description", "name", "body"}


def translate_model(model: Any, gettext: Callable[[str], str]) -> Any:
    """A copy of ``model`` with every docstring, description and state name
    passed through ``gettext`` (msgid: the text as written in the code)."""
    return _translate(copy.deepcopy(model), gettext)


def _translate(value: Any, gettext: Callable[[str], str]) -> Any:
    from resource_engine_devtools.documentation.docstrings import DocText
    from resource_engine_devtools.documentation.model import StateDoc

    if isinstance(value, DocText):
        value.summary = _tr(value.summary, gettext)
        value.description = _tr(value.description, gettext)
        value.sections = {k: _tr(v, gettext) or v for k, v in value.sections.items()}
        value.items = {
            k: {name: _tr(text, gettext) or text for name, text in items.items()}
            for k, items in value.items.items()
        }
        return value
    if isinstance(value, StateDoc):
        value.name = _tr(value.name, gettext) or value.name
        value.description = _tr(value.description, gettext)
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            current = getattr(value, f.name)
            if f.name == "description" and isinstance(current, str):
                setattr(value, f.name, _tr(current, gettext))
            elif isinstance(current, list | dict) or dataclasses.is_dataclass(current):
                setattr(value, f.name, _translate(current, gettext))
        return value
    if isinstance(value, list):
        return [_translate(v, gettext) for v in value]
    if isinstance(value, dict):
        return {k: _translate(v, gettext) for k, v in value.items()}
    return value


def _tr(text: str | None, gettext: Callable[[str], str]) -> str | None:
    if not text:
        return text
    return gettext(text) or text


def model_texts(model: Any) -> Iterator[tuple[str, str]]:
    """``(text, where)`` for every application text of the model, for extraction."""
    seen: set[str] = set()

    def visit(value: Any, where: str) -> Iterator[tuple[str, str]]:
        from resource_engine_devtools.documentation.docstrings import DocText
        from resource_engine_devtools.documentation.model import StateDoc

        if isinstance(value, DocText):
            texts = [value.summary, value.description, *value.sections.values()]
            texts += [t for items in value.items.values() for t in items.values()]
            for text in texts:
                if text and text not in seen:
                    seen.add(text)
                    yield text, where
            return
        if isinstance(value, StateDoc):
            for text in (value.name if value.name != value.id else None, value.description):
                if text and text not in seen:
                    seen.add(text)
                    yield text, f"{where} state {value.id}"
            return
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            label = getattr(value, "name", None) or getattr(value, "template", None)
            here = f"{where} {label}".strip() if isinstance(label, str) else where
            for f in dataclasses.fields(value):
                current = getattr(value, f.name)
                if f.name == "description" and isinstance(current, str) and current:
                    if current not in seen:
                        seen.add(current)
                        yield current, here
                elif isinstance(current, list | dict) or dataclasses.is_dataclass(current):
                    yield from visit(current, here)
        elif isinstance(value, list):
            for item in value:
                yield from visit(item, where)
        elif isinstance(value, dict):
            for item in value.values():
                yield from visit(item, where)

    resources = [r for r in getattr(model, "resources", []) if not _engine(r)]
    yield from visit(resources, "")
    yield from visit(getattr(model, "system_roles", []), "role")


def _engine(resource: Any) -> bool:
    """The engine's built-in resources (authz_*, audit_log, documents) keep
    their own texts: they aren't the application's to translate."""
    return str(getattr(resource, "module", "")).startswith("resource_engine.")
