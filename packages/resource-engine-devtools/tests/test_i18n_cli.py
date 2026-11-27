"""``resource-engine i18n``: extract, update, compile, status and export on a
small project, and the catalogues shipped with the engine and devtools."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest
from babel.messages.extract import extract_from_file
from babel.messages.pofile import read_po
from resource_engine_devtools.cli import main

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [tool.resource-engine]
            paths = ["src"]

            [tool.resource-engine.i18n]
            directory = "src/shop/locale"
            source_locale = "ca"
            locales = ["ca", "en"]
            """
        )
    )
    package = tmp_path / "src" / "shop"
    (package / "templates" / "email").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "texts.py").write_text(
        "from resource_engine.i18n import gettext as _\n\n"
        "def greeting():\n    # i18n: shown on the home page\n    return _('Bon dia')\n"
    )
    (package / "templates" / "email" / "hi.txt").write_text("{% trans %}Fins aviat{% endtrans %}\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*args) -> int:
    return main(["i18n", *args])


def test_the_translation_workflow(project, capsys):
    assert run("extract") == 0
    pot = project / "src/shop/locale/messages.pot"
    ids = {m.id for m in read_po(pot.open("rb")) if m.id}
    assert ids == {"Bon dia", "Fins aviat"}
    assert "shown on the home page" in pot.read_text()

    assert run("update") == 0
    po = project / "src/shop/locale/en/LC_MESSAGES/messages.po"
    assert po.is_file() and not (project / "src/shop/locale/ca").exists()  # the source
    capsys.readouterr()
    assert run("status", "--strict", "--json") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["locales"][0]["missing"] == ["Bon dia", "Fins aviat"]

    po.write_text(
        po.read_text()
        .replace('msgid "Bon dia"\nmsgstr ""', 'msgid "Bon dia"\nmsgstr "Good morning"')
        .replace('msgid "Fins aviat"\nmsgstr ""', 'msgid "Fins aviat"\nmsgstr "See you"')
    )
    assert run("compile") == 0
    assert run("status", "--strict") == 0

    from resource_engine.i18n import Translations

    translations = Translations(project / "src/shop/locale", default_locale="ca")
    assert translations.gettext("Bon dia", "en") == "Good morning"

    # a changed text: kept as fuzzy until someone checks it
    (project / "src/shop/texts.py").write_text(
        "from resource_engine.i18n import gettext as _\n\ndef greeting():\n"
        "    return _('Bon dia!')\n"
    )
    assert run("extract") == 0 and run("update") == 0
    entry = next(m for m in read_po(po.open("rb")) if m.id == "Bon dia!")
    assert entry.fuzzy and entry.string == "Good morning"

    assert run("export", "--out", "out") == 0
    exported = json.loads((project / "out/en.json").read_text())
    assert exported["messages"]["Fins aviat"] == "See you"
    assert exported["errors"]["RE-E102"] == "The record does not exist."
    catalan = json.loads((project / "out/ca.json").read_text())
    assert catalan["errors"]["RE-E102"] == "El registre no existeix."
    assert catalan["messages"]["Fins aviat"] == "Fins aviat"


def test_without_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert run("status") == 2
    assert "tool.resource-engine.i18n" in capsys.readouterr().out


def test_the_engines_compiled_catalogues_match_their_sources():
    from resource_engine.i18n import engine_translations

    engine = engine_translations()
    root = REPO / "packages/resource-engine/src/resource_engine/locale"
    for po in root.glob("*/LC_MESSAGES/resource_engine.po"):
        locale = po.parts[-3]
        for m in read_po(po.open("rb")):
            if m.id:
                assert engine.pgettext(m.context, m.id, locale) == m.string, (locale, m.id)
        assert os.path.getmtime(po.with_suffix(".mo")) >= 0


def test_every_generator_text_is_translated():
    base = REPO / "packages/resource-engine-devtools/src/resource_engine_devtools/documentation"
    texts = set()
    for name in ("markdown.py", "site.py", "api.py"):
        for _, message, _, _ in extract_from_file(
            "python", str(base / "generators" / name), keywords={"t": None}
        ):
            texts.add(message)
    from resource_engine_devtools.documentation.docstrings import FUNCTIONAL_SECTIONS

    texts |= set(FUNCTIONAL_SECTIONS)
    for locale in ("ca", "es"):
        catalog = read_po((base / "locale" / locale / "LC_MESSAGES" / "docs.po").open("rb"))
        translated = {m.id for m in catalog if m.id and m.string}
        assert not texts - translated, (locale, sorted(texts - translated))
