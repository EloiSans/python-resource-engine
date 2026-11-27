"""JinjaTemplates: locale fallback, inheritance, autoescaping, strict
variables and the sandbox, with the Mailer on top."""

from __future__ import annotations

import pytest
from resource_engine.email import Mailer
from resource_engine.errors import TemplateFailed, TemplateNotFound
from resource_engine.testing import RecordingEmailSender
from resource_engine_jinja import JinjaTemplates

FILES = {
    "email/base.html": "<html><body>{% block body %}{% endblock %}<p>{{ app }}</p></body></html>",
    "email/approved.subject.txt": "Invoice {{ number }} approved",
    "email/approved.txt": "Invoice {{ number }} for {{ customer }}: {{ amount }}",
    "email/approved.html": (
        '{% extends "email/base.html" %}{% block body %}<h1>{{ customer }}</h1>'
        "{% for line in lines %}<li>{{ line }}</li>{% endfor %}{% endblock %}"
    ),
    "ca/email/approved.subject.txt": "Factura {{ number }} aprovada",
    "ca-ES/email/approved.txt": "Factura {{ number }} de {{ customer }}: {{ amount }} €",
    "email/broken.txt": "{% if %}",
    "email/sneaky.txt": "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "email/includes_missing.txt": "{% include 'email/nope.txt' %}",
}


@pytest.fixture
def templates(tmp_path):
    for name, text in FILES.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return JinjaTemplates(tmp_path, globals={"app": "Factures"}, default_locale="en")


def test_locales_fall_back_from_specific_to_unlocalised(templates):
    context = {"number": 7, "customer": "ACME", "amount": "12.50"}
    assert templates.render("email/approved.txt", context, locale="ca-ES").endswith("€")
    assert templates.render("email/approved.txt", context, locale="ca") == (
        "Invoice 7 for ACME: 12.50"
    )
    assert templates.render("email/approved.subject.txt", context, locale="ca-ES") == (
        "Factura 7 aprovada"
    )
    assert templates.exists("email/approved.html", locale="fr")
    assert not templates.exists("email/nope.txt")
    with pytest.raises(TemplateNotFound) as info:
        templates.render("email/nope.txt", {}, locale="ca")
    assert info.value.details["tried"] == [
        "ca/email/nope.txt",
        "en/email/nope.txt",
        "email/nope.txt",
    ]
    assert (
        "email/approved.txt" in templates.names()
        and "ca/email/approved.txt" not in templates.names()
    )


def test_html_is_escaped_and_inherits_the_layout(templates):
    html = templates.render("email/approved.html", {"customer": "<script>", "lines": ["a & b"]})
    assert "<h1>&lt;script&gt;</h1>" in html and "<li>a &amp; b</li>" in html
    assert html.startswith("<html>") and "<p>Factures</p>" in html
    text = templates.render("email/approved.txt", {"number": 1, "customer": "<b>", "amount": 1})
    assert "<b>" in text  # plain text is not escaped


def test_errors(templates):
    with pytest.raises(TemplateFailed):
        templates.render("email/approved.txt", {"number": 1})  # customer missing
    with pytest.raises(TemplateFailed):
        templates.render("email/broken.txt", {})
    with pytest.raises(TemplateFailed):
        templates.render("email/sneaky.txt", {})  # the sandbox refuses it
    with pytest.raises(TemplateFailed):
        templates.render("email/includes_missing.txt", {})
    with pytest.raises(ValueError):
        JinjaTemplates()


def test_the_mailer_on_jinja(templates):
    sender = RecordingEmailSender()
    mailer = Mailer(sender, templates, from_address="factures@example.com")
    message = mailer.compose(
        "approved", "a@example.com", {"number": 7, "customer": "ACME", "amount": 3, "lines": []},
        locale="ca-ES",
    )  # fmt: skip
    assert message.subject == "Factura 7 aprovada"
    assert message.text == "Factura 7 de ACME: 3 €"
    assert "<h1>ACME</h1>" in message.html


def test_templates_shipped_in_a_package():
    templates = JinjaTemplates(package="invoices", folder="templates")
    assert templates.exists("email/invoice_approved.subject.txt", locale="ca")


def test_templates_translate_with_the_catalogue_and_escape_values(tmp_path):
    from babel.messages.catalog import Catalog
    from babel.messages.mofile import write_mo
    from resource_engine.i18n import Translations

    catalog = Catalog(locale="en")
    catalog.add("Hola, %(customer)s:", "Hello %(customer)s,")
    catalog.add("Factura aprovada", "Invoice approved")
    folder = tmp_path / "locale" / "en" / "LC_MESSAGES"
    folder.mkdir(parents=True)
    with (folder / "messages.mo").open("wb") as handle:
        write_mo(handle, catalog)
    (tmp_path / "t" / "email").mkdir(parents=True)
    (tmp_path / "t" / "email" / "hi.html").write_text(
        "<p>{% trans %}Hola, {{ customer }}:{% endtrans %}</p><p>{{ _('Factura aprovada') }}</p>"
    )
    templates = JinjaTemplates(
        tmp_path / "t",
        default_locale="ca",
        translations=Translations(tmp_path / "locale", default_locale="ca"),
    )
    english = templates.render("email/hi.html", {"customer": "<b>ACME</b>"}, locale="en")
    assert english == "<p>Hello &lt;b&gt;ACME&lt;/b&gt;,</p><p>Invoice approved</p>"
    catalan = templates.render("email/hi.html", {"customer": "ACME"}, locale="ca")
    assert catalan == "<p>Hola, ACME:</p><p>Factura aprovada</p>"  # the source language
    assert templates.variants("email/hi.html") == ["ca", "en"]
    assert "Hello <mark" in templates.preview("email/hi.html", locale="en")
