"""``resource-engine i18n``: the application's translation catalogues.

```toml
[tool.resource-engine.i18n]
directory = "invoices/locale"     # <locale>/LC_MESSAGES/messages.po and .mo
source_locale = "ca"              # the language the code's texts are written in
locales = ["ca", "en", "es"]
```

- ``extract``: ``messages.pot`` from the code (``_()``, ``gettext``,
  ``ngettext``, ``pgettext``), the Jinja templates (``{% trans %}``, ``_()``)
  and the documentation texts (docstrings, descriptions, state names).
- ``update``: creates or updates ``<locale>/LC_MESSAGES/messages.po`` for every
  locale but the source one, keeping translations and marking changed texts fuzzy.
- ``compile``: ``.po`` → ``.mo`` for the runtime (``resource_engine.i18n``).
- ``status``: translated, fuzzy and missing per locale; ``--strict`` fails on gaps.
- ``export``: a JSON catalogue per locale for front ends (the application's
  messages and the engine's error titles by code).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po

KEYWORDS = {
    "_": None,
    "gettext": None,
    "ngettext": (1, 2),
    "pgettext": ((1, "c"), 2),
    "lazy_gettext": None,
}
TEMPLATE_EXTENSIONS = (".html", ".txt", ".xml", ".md")


@dataclass
class I18nConfig:
    directory: Path
    source_locale: str = "en"
    locales: list[str] = field(default_factory=list)
    domain: str = "messages"

    @property
    def targets(self) -> list[str]:
        """Locales that need a catalogue (all but the source language)."""
        return [loc for loc in self.locales if loc != self.source_locale]

    def po(self, locale: str) -> Path:
        return self.directory / locale / "LC_MESSAGES" / f"{self.domain}.po"

    def mo(self, locale: str) -> Path:
        return self.directory / locale / "LC_MESSAGES" / f"{self.domain}.mo"

    @property
    def pot(self) -> Path:
        return self.directory / f"{self.domain}.pot"


def load_config(config: dict[str, Any], root: Path) -> I18nConfig | None:
    section = config.get("i18n")
    if not section:
        return None
    source = section.get("source_locale", "en")
    locales = list(section.get("locales") or [source])
    if source not in locales:
        locales.insert(0, source)
    return I18nConfig(
        directory=root / section.get("directory", "locale"),
        source_locale=source,
        locales=locales,
        domain=section.get("domain", "messages"),
    )


# ------------------------------------------------------------------ extract


def extract(
    config: I18nConfig, paths: list[Path], *, model: Any = None, project: str = "application"
) -> Catalog:
    """Every translatable text of the application, as a template catalogue."""
    from resource_engine_devtools.documentation.i18n import model_texts

    catalog = Catalog(project=project, locale=None, domain=config.domain, fuzzy=False)
    method_map = [
        ("**/templates/**.html", "jinja2.ext:babel_extract"),
        ("**/templates/**.txt", "jinja2.ext:babel_extract"),
        ("**/templates/**.xml", "jinja2.ext:babel_extract"),
        ("**/tests/**", "ignore"),
        ("**/migrations/**", "ignore"),
        ("**.py", "python"),
    ]
    options = {"**/templates/**": {"extensions": "jinja2.ext.i18n", "trimmed": "true"}}
    for base in paths:
        for filename, lineno, message, comments, context in extract_from_dir(
            str(base), method_map, options, keywords=KEYWORDS, comment_tags=("i18n:",)
        ):
            location = (str(Path(base, filename)), lineno)
            catalog.add(message, locations=[location], auto_comments=comments, context=context)
    if model is not None:
        for text, where in model_texts(model):
            catalog.add(text, auto_comments=[f"docs:{where}".rstrip(":")])
    return catalog


def write_template(config: I18nConfig, catalog: Catalog) -> Path:
    config.directory.mkdir(parents=True, exist_ok=True)
    with config.pot.open("wb") as handle:
        write_po(handle, catalog, width=100, sort_output=True)
    return config.pot


# ------------------------------------------------------------------ update


def update(config: I18nConfig) -> dict[str, str]:
    """Create or update each target locale's ``.po`` from the template."""
    with config.pot.open("rb") as handle:
        template = read_po(handle)
    done = {}
    for locale in config.targets:
        path = config.po(locale)
        if path.exists():
            with path.open("rb") as handle:
                catalog = read_po(handle, locale=locale)
            catalog.update(template, no_fuzzy_matching=False)
            done[locale] = "updated"
        else:
            catalog = Catalog(locale=locale, domain=config.domain, project=template.project)
            for message in template:
                if message.id:
                    catalog.add(
                        message.id,
                        locations=message.locations,
                        auto_comments=message.auto_comments,
                        context=message.context,
                    )
            done[locale] = "created"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            write_po(handle, catalog, width=100, sort_output=True, ignore_obsolete=True)
    return done


# ----------------------------------------------------------------- compile


def compile_catalogues(config: I18nConfig) -> dict[str, Path]:
    """``.po`` → ``.mo`` (fuzzy entries left out, as gettext does)."""
    written = {}
    for locale in config.targets:
        path = config.po(locale)
        if not path.exists():
            continue
        with path.open("rb") as handle:
            catalog = read_po(handle, locale=locale)
        with config.mo(locale).open("wb") as handle:
            write_mo(handle, catalog, use_fuzzy=False)
        written[locale] = config.mo(locale)
    return written


@dataclass
class LocaleStatus:
    locale: str
    total: int
    translated: int
    fuzzy: int
    missing: list[str]
    compiled: bool  # the .mo holds exactly the .po's translations

    def to_dict(self) -> dict[str, Any]:
        return {
            "locale": self.locale,
            "total": self.total,
            "translated": self.translated,
            "fuzzy": self.fuzzy,
            "missing": self.missing,
            "compiled": self.compiled,
        }


def status(config: I18nConfig) -> list[LocaleStatus]:
    out = []
    for locale in config.targets:
        path = config.po(locale)
        if not path.exists():
            out.append(LocaleStatus(locale, 0, 0, 0, ["(no catalogue: run i18n update)"], False))
            continue
        with path.open("rb") as handle:
            catalog = read_po(handle, locale=locale)
        messages = [m for m in catalog if m.id]
        missing = [
            str(m.id if isinstance(m.id, str) else m.id[0]) for m in messages if not m.string
        ]
        fuzzy = sum(1 for m in messages if m.fuzzy and m.string)
        compiled = _compiled(config.mo(locale), messages)
        out.append(
            LocaleStatus(
                locale,
                len(messages),
                len(messages) - len(missing) - fuzzy,
                fuzzy,
                missing,
                compiled,
            )
        )
    return out


def _compiled(mo: Path, messages: list[Any]) -> bool:
    """Whether the ``.mo`` has the ``.po``'s current translations (by content:
    file times mean nothing after a git checkout)."""
    import gettext

    if not mo.exists():
        return False
    with mo.open("rb") as handle:
        catalogue = gettext.GNUTranslations(handle)._catalog  # type: ignore[attr-defined]
    for m in messages:
        if not m.string or m.fuzzy:
            continue
        key = m.id if isinstance(m.id, str) else (m.id[0], 0)
        if m.context:
            key = f"{m.context}\x04{m.id}"
        found = catalogue.get(key)
        expected = m.string if isinstance(m.string, str) else m.string[0]
        if found != expected:
            return False
    return True


# ------------------------------------------------------------- application


def po_gettext(config: I18nConfig, locale: str) -> Callable[[str], str]:
    """The application's texts in ``locale``, read from its ``.po`` (no compile
    step needed for the docs). The source locale returns texts unchanged."""
    if locale == config.source_locale or locale.split("-")[0] == config.source_locale:
        return lambda text: text
    texts: dict[str, str] = {}
    for candidate in (locale, locale.split("-")[0]):
        path = config.po(candidate)
        if path.exists():
            with path.open("rb") as handle:
                for m in read_po(handle, locale=candidate):
                    if m.id and m.string and not m.fuzzy and isinstance(m.id, str):
                        texts.setdefault(m.id, str(m.string))
    return lambda text: texts.get(text, text)


def export(config: I18nConfig, out: Path) -> list[Path]:
    """One JSON file per locale for front ends: the application's messages and
    the engine's error titles, by code."""
    from resource_engine.errors import TITLES
    from resource_engine.i18n import engine_translations

    engine = engine_translations()
    written = []
    out.mkdir(parents=True, exist_ok=True)
    for locale in config.locales:
        gettext = po_gettext(config, locale)
        messages: dict[str, str] = {}
        path = config.po(locale)
        source = path if path.exists() else config.pot
        if source.exists():
            with source.open("rb") as handle:
                for m in read_po(handle):
                    if m.id and isinstance(m.id, str) and not m.context:
                        messages[m.id] = gettext(m.id)
        errors = {code: engine.pgettext(code, title, locale) for code, title in TITLES.items()}
        target = out / f"{locale}.json"
        target.write_text(
            json.dumps(
                {"locale": locale, "messages": messages, "errors": errors},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        written.append(target)
    return written


def iter_docs_locales(config: I18nConfig | None, wanted: list[str] | None) -> Iterator[str]:
    """The locales to render the docs in: the requested ones, or every configured one."""
    if wanted:
        yield from wanted
    elif config is not None:
        yield from config.locales
