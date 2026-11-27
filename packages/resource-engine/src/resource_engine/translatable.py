"""Translatable data: a field that holds one text per language (a product
name, a category description), stored as ``{locale: text}``.

```python
@dataclass
class Product:
    sku: str
    name: dict[str, str] = field(default_factory=dict)   # {"ca": "Cadira", "en": "Chair"}


class ProductRead(BaseModel):
    sku: str
    name: str | None                          # in the request's language
    name_translations: dict[str, str] = {}    # optional: every language


class ProductResource(Resource):
    translatable = ["name"]
    default_locale = "ca"
```

- **Reading** gives the text in the request's language (``Accept-Language``,
  the token's ``locale``), falling back to its base language (``ca-ES`` →
  ``ca``), then ``default_locale``, then any language there is.
- **Writing** ``name`` sets the request's language (or ``default_locale``)
  and keeps the others; ``name_translations`` (when the create or update
  schema declares it) sets several at once, and ``null`` for a language
  removes it.
- **Filtering and sorting** by a translatable field use the same language
  and fallbacks, in memory and in SQL (``JSON`` columns).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from resource_engine.i18n import get_locale
from resource_engine.templates import locale_candidates


def locale_chain(default: str | None) -> list[str]:
    """The languages to try, most specific first, for the current request."""
    return [c for c in locale_candidates(get_locale(), default) if c]


def localise(value: Any, chain: Iterable[str]) -> Any:
    """The text of ``value`` (``{locale: text}``) for the first language of
    ``chain`` that has one; any language as a last resort."""
    if not isinstance(value, Mapping):
        return value
    for locale in chain:
        text = value.get(locale)
        if text not in (None, ""):
            return text
    return next((v for v in value.values() if v not in (None, "")), None)


def write_locale(default: str | None) -> str:
    return get_locale() or default or "en"


def merge(
    current: Any, text: Any, translations: Mapping[str, Any] | None, locale: str
) -> dict[str, str]:
    """``current`` with ``text`` in ``locale`` and the ``translations`` applied
    (``None`` removes a language)."""
    values = dict(current) if isinstance(current, Mapping) else {}
    for key, value in (translations or {}).items():
        if value is None:
            values.pop(key, None)
        else:
            values[str(key)] = str(value)
    if text is not None:
        values[locale] = str(text)
    return values


__all__ = ["locale_chain", "localise", "merge", "write_locale"]
