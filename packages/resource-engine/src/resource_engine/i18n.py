"""Translations: gettext catalogues per locale and the locale of the current
request, with the standard library only (compiled ``.mo`` files).

```python
from resource_engine.i18n import Translations, set_translations, use_locale, gettext as _

set_translations(Translations("invoices/locale", default_locale="ca"))
with use_locale("en"):
    _("Factura aprovada")          # → "Invoice approved"
```

- **Catalogues:** ``<directory>/<locale>/LC_MESSAGES/<domain>.mo``, written
  by ``resource-engine i18n compile`` from the ``.po`` files translators edit.
- **Locale:** a context variable, set per request by the HTTP adapter
  (``Accept-Language``, or the principal's ``locale`` claim) and per message by
  the mailer. ``ca-ES`` falls back to ``ca``, then to the default locale, then
  to the text as written in the code.
- **Engine messages:** the engine's own texts (error titles) have their own
  catalogue (domain ``resource_engine``), shipped with the package; an
  application catalogue can override them.

See docs/i18n.md.
"""

from __future__ import annotations

import gettext as _gettext
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path

from resource_engine.templates import locale_candidates

ENGINE_DOMAIN = "resource_engine"

_locale: ContextVar[str | None] = ContextVar("resource_engine_locale", default=None)


def get_locale() -> str | None:
    """The locale of the current request or message, if any."""
    return _locale.get()


@contextmanager
def use_locale(locale: str | None) -> Iterator[None]:
    """Run a block in ``locale`` (``None`` keeps the texts as written)."""
    token = _locale.set(locale)
    try:
        yield
    finally:
        _locale.reset(token)


def set_locale(locale: str | None) -> None:
    """Set the locale for the rest of the current context (a request)."""
    _locale.set(locale)


class Translations:
    """Compiled catalogues of one domain, per locale.

    Args:
        directory: Holds ``<locale>/LC_MESSAGES/<domain>.mo``.
        domain: The catalogue name (``messages``).
        default_locale: Tried after the requested locale.
        locales: The locales offered (for negotiation); by default the
            folders found in ``directory``.
        fallback: Another ``Translations`` asked when this one has no
            translation (the engine's catalogue behind the application's).
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        domain: str = "messages",
        default_locale: str | None = None,
        locales: Sequence[str] | None = None,
        fallback: Translations | None = None,
    ) -> None:
        self.directory = Path(str(directory))
        self.domain = domain
        self.default_locale = default_locale
        self.fallback = fallback
        found = sorted(
            p.name
            for p in (self.directory.iterdir() if self.directory.is_dir() else ())
            if (p / "LC_MESSAGES" / f"{domain}.mo").is_file()
        )
        self.locales = list(locales) if locales is not None else found
        self._catalogues: dict[str, _gettext.NullTranslations | None] = {}

    def _catalogue(self, locale: str) -> _gettext.NullTranslations | None:
        if locale not in self._catalogues:
            path = self.directory / locale.replace("-", "_") / "LC_MESSAGES" / f"{self.domain}.mo"
            if not path.is_file():
                path = self.directory / locale / "LC_MESSAGES" / f"{self.domain}.mo"
            catalogue = None
            if path.is_file():
                with path.open("rb") as handle:
                    catalogue = _gettext.GNUTranslations(handle)
            self._catalogues[locale] = catalogue
        return self._catalogues[locale]

    def _lookup(self, key: str, locale: str | None) -> str | None:
        for candidate in locale_candidates(locale, self.default_locale)[:-1]:
            catalogue = self._catalogue(candidate)
            if catalogue is None:
                continue
            found = catalogue._catalog.get(key)  # type: ignore[attr-defined]
            if isinstance(found, str) and found:
                return found
        return None

    def gettext(self, message: str, locale: str | None = None) -> str:
        """``message`` in ``locale`` (the current one by default)."""
        locale = locale if locale is not None else get_locale()
        found = self._lookup(message, locale)
        if found is not None:
            return found
        return self.fallback.gettext(message, locale) if self.fallback else message

    def pgettext(self, context: str, message: str, locale: str | None = None) -> str:
        """``message`` disambiguated by ``context`` (gettext's ``msgctxt``)."""
        locale = locale if locale is not None else get_locale()
        found = self._lookup(f"{context}\x04{message}", locale)
        if found is not None:
            return found
        return self.fallback.pgettext(context, message, locale) if self.fallback else message

    def ngettext(self, singular: str, plural: str, n: int, locale: str | None = None) -> str:
        locale = locale if locale is not None else get_locale()
        for candidate in locale_candidates(locale, self.default_locale)[:-1]:
            catalogue = self._catalogue(candidate)
            if catalogue is not None and (singular, 0) in catalogue._catalog:  # type: ignore[attr-defined]
                return catalogue.ngettext(singular, plural, n)
        if self.fallback:
            return self.fallback.ngettext(singular, plural, n, locale)
        return singular if n == 1 else plural

    def negotiate(self, accept_language: str | None) -> str | None:
        """The best offered locale for an ``Accept-Language`` header."""
        return negotiate(accept_language, self.offered())

    def offered(self) -> list[str]:
        locales = list(self.locales)
        if self.default_locale and self.default_locale not in locales:
            locales.insert(0, self.default_locale)
        return locales


def negotiate(accept_language: str | None, offered: Sequence[str]) -> str | None:
    """``ca-ES,ca;q=0.9,en;q=0.5`` against ``["en", "ca"]`` → ``"ca"``.

    Exact matches win, then the primary language (``ca-ES`` → ``ca``, and
    ``pt`` → ``pt-BR`` when only that is offered). ``q=0`` refuses a language.
    """
    if not accept_language or not offered:
        return None
    wanted = []
    for position, part in enumerate(accept_language.split(",")):
        tag, _, params = part.strip().partition(";")
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if tag and quality > 0:
            wanted.append((-quality, position, tag.strip().replace("_", "-")))
    lower = {o.lower(): o for o in offered}
    for _, _, tag in sorted(wanted):
        if tag == "*":
            return offered[0]
        if tag.lower() in lower:
            return lower[tag.lower()]
        primary = tag.split("-")[0].lower()
        if primary in lower:
            return lower[primary]
        for option in offered:
            if option.lower().split("-")[0] == primary:
                return option
    return None


def engine_translations() -> Translations:
    """The engine's own catalogue (error titles), shipped with the package."""
    return Translations(Path(str(files("resource_engine") / "locale")), domain=ENGINE_DOMAIN)


_active: Translations = engine_translations()


def set_translations(translations: Translations | None) -> None:
    """Install the application's catalogue (the engine's stays behind it)."""
    global _active
    engine = engine_translations()
    if translations is None:
        _active = engine
        return
    if translations.fallback is None and translations.domain != ENGINE_DOMAIN:
        translations.fallback = engine
    _active = translations


def get_translations() -> Translations:
    return _active


def gettext(message: str) -> str:
    """``message`` in the current locale, from the installed catalogue."""
    return _active.gettext(message)


def pgettext(context: str, message: str) -> str:
    return _active.pgettext(context, message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _active.ngettext(singular, plural, n)


_ = gettext

__all__ = [
    "ENGINE_DOMAIN",
    "Translations",
    "engine_translations",
    "get_locale",
    "get_translations",
    "gettext",
    "negotiate",
    "ngettext",
    "pgettext",
    "set_locale",
    "set_translations",
    "use_locale",
]
