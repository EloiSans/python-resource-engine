"""Translations: negotiation, catalogues with fallback, the current locale,
and error titles from the engine's own catalogue."""

from __future__ import annotations

import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from resource_engine.errors import TITLES, EntityNotFound, PermissionDenied
from resource_engine.i18n import (
    Translations,
    engine_translations,
    get_locale,
    gettext,
    negotiate,
    set_translations,
    use_locale,
)


@pytest.mark.parametrize(
    ("header", "offered", "expected"),
    [
        ("ca-ES,ca;q=0.9,en;q=0.5", ["en", "ca"], "ca"),
        ("en-GB", ["ca", "en"], "en"),
        ("pt", ["ca", "pt-BR"], "pt-BR"),
        ("fr, en;q=0.1", ["ca", "en"], "en"),
        ("ca;q=0, en", ["ca", "en"], "en"),
        ("*", ["es", "en"], "es"),
        ("de", ["ca", "en"], None),
        ("", ["ca"], None),
        ("es;q=bad, en", ["es", "en"], "en"),
    ],
)
def test_negotiation(header, offered, expected):
    assert negotiate(header, offered) == expected


@pytest.fixture
def catalogues(tmp_path):
    for locale, entries in {
        "en": {"Factura aprovada": "Invoice approved", "Hola": "Hello"},
        "es": {"Factura aprovada": "Factura aprobada"},
    }.items():
        catalog = Catalog(locale=locale)
        for msgid, msgstr in entries.items():
            catalog.add(msgid, msgstr)
        catalog.add("RE-E102-override", "x")
        catalog.add("The record does not exist.", "Not here.", context="RE-E102")
        folder = tmp_path / locale / "LC_MESSAGES"
        folder.mkdir(parents=True)
        with (folder / "messages.mo").open("wb") as handle:
            write_mo(handle, catalog)
    return Translations(tmp_path, default_locale="ca")


def test_catalogues_fall_back_from_specific_to_the_text_as_written(catalogues):
    assert catalogues.locales == ["en", "es"]
    assert catalogues.offered() == ["ca", "en", "es"]
    assert catalogues.gettext("Factura aprovada", "en-GB") == "Invoice approved"
    assert catalogues.gettext("Hola", "es") == "Hola"  # not in es, no es → as written
    assert catalogues.gettext("Hola", "fr") == "Hola"
    with use_locale("en"):
        assert get_locale() == "en" and catalogues.gettext("Hola") == "Hello"
    assert get_locale() is None
    assert catalogues.negotiate("es-ES,en") == "es"
    assert catalogues.ngettext("una", "%(n)s", 2, "en") == "%(n)s"


def test_error_titles_follow_the_locale_and_the_application_can_override_them(catalogues):
    assert set(TITLES) >= {"RE-E000", "RE-E102", "RE-E710"}
    error = EntityNotFound("no invoice 7")
    assert error.to_dict()["title"] == "The record does not exist."
    assert error.to_dict()["message"] == "no invoice 7"  # technical, never translated
    with use_locale("ca"):
        assert error.title == "El registre no existeix."
        assert PermissionDenied("x").title == "No tens permís per fer això."
    with use_locale("es-ES"):
        assert error.title == "El registro no existe."
    set_translations(catalogues)
    try:
        with use_locale("en"):
            assert error.title == "Not here."  # the application's catalogue wins
            assert gettext("Factura aprovada") == "Invoice approved"
        with use_locale("ca"):
            assert error.title == "El registre no existeix."  # the engine's behind it
    finally:
        set_translations(None)


def test_every_error_title_is_translated_in_the_shipped_catalogues():
    engine = engine_translations()
    assert sorted(engine.locales) == ["ca", "es"]
    for locale in engine.locales:
        for code, title in TITLES.items():
            assert engine.pgettext(code, title, locale) != title, (locale, code)
