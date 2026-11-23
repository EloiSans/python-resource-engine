"""Docstring convention (Google style with functional sections), parsed with docstring-parser."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

from docstring_parser.google import GoogleParser, Section, SectionType

#: Functional sections understood by the engine, by docstring title → key.
FUNCTIONAL_SECTIONS: dict[str, str] = {
    "Business area": "business_area",
    "Actors": "actors",
    "Preconditions": "preconditions",
    "Postconditions": "postconditions",
    "Business Rules": "business_rules",
    "Effects": "effects",
    "Exceptions": "exceptions",
    "Examples": "examples",
}
#: Sections whose body is a ``name: description`` list.
KEYED_SECTIONS: dict[str, str] = {"States": "states", "Transitions": "transitions"}

_PARSER = GoogleParser(
    sections=[Section(t, k, SectionType.SINGULAR) for t, k in FUNCTIONAL_SECTIONS.items()]
    + [Section(t, k, SectionType.MULTIPLE) for t, k in KEYED_SECTIONS.items()],
    title_colon=True,
)


@dataclass
class DocText:
    summary: str | None = None
    description: str | None = None
    sections: dict[str, str] = field(default_factory=dict)
    items: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def present(self) -> bool:
        return bool(self.summary)

    def list_section(self, key: str) -> list[str]:
        raw = self.sections.get(key, "")
        return [line.strip().lstrip("-*").strip() for line in raw.splitlines() if line.strip()]


def parse_doc(obj_or_text: Any) -> DocText:
    text = obj_or_text if isinstance(obj_or_text, str) else _own_doc(obj_or_text)
    if not text:
        return DocText()
    parsed = _PARSER.parse(inspect.cleandoc(text))
    doc = DocText(summary=parsed.short_description, description=parsed.long_description)
    for meta in parsed.meta:
        key = meta.args[0]
        if len(meta.args) > 1:
            doc.items.setdefault(key, {})[meta.args[1]] = (meta.description or "").strip()
        else:
            doc.sections[key] = (meta.description or "").strip()
    return doc


def _own_doc(obj: Any) -> str | None:
    """Docstring defined on the object itself, not inherited from a base class."""
    if inspect.isclass(obj):
        return obj.__dict__.get("__doc__")
    return getattr(obj, "__doc__", None)
