"""The language of each request: Accept-Language, the user's locale claim,
Content-Language and translated error titles."""

from __future__ import annotations

import httpx
from fastapi import Depends, FastAPI
from resource_engine.errors import EntityNotFound
from resource_engine.i18n import Translations, get_locale
from resource_engine.ports.identity import Principal
from resource_engine_fastapi import bearer_actor, install_exception_handlers, install_locale


class Tokens:
    async def authenticate(self, token: str) -> Principal:
        claims = {"locale": token} if token != "none" else {}
        return Principal(subject="u", issuer="i", claims=claims)


def client(tmp_path) -> httpx.AsyncClient:
    for locale in ("en", "es"):
        (tmp_path / locale / "LC_MESSAGES").mkdir(parents=True)
        (tmp_path / locale / "LC_MESSAGES" / "messages.mo").write_bytes(_empty_mo())
    translations = Translations(tmp_path, default_locale="ca")
    app = FastAPI()
    install_exception_handlers(app)
    install_locale(app, translations)
    actor = bearer_actor(Tokens(), translations=translations)

    @app.get("/locale")
    async def locale():
        return {"locale": get_locale()}

    @app.get("/me")
    async def me(principal: Principal = Depends(actor)):
        return {"locale": get_locale()}

    @app.get("/missing")
    async def missing():
        raise EntityNotFound("no invoice 7")

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _empty_mo() -> bytes:
    import io

    from babel.messages.catalog import Catalog
    from babel.messages.mofile import write_mo

    buffer = io.BytesIO()
    write_mo(buffer, Catalog())
    return buffer.getvalue()


async def test_accept_language_picks_an_offered_locale(tmp_path):
    api = client(tmp_path)
    english = await api.get("/locale", headers={"Accept-Language": "en-GB,en;q=0.9"})
    assert english.json() == {"locale": "en"} and english.headers["content-language"] == "en"
    default = await api.get("/locale", headers={"Accept-Language": "de"})
    assert default.json() == {"locale": "ca"}  # not offered: the default locale
    assert (await api.get("/locale")).headers["content-language"] == "ca"


async def test_error_titles_in_the_request_language(tmp_path):
    api = client(tmp_path)
    spanish = (await api.get("/missing", headers={"Accept-Language": "es"})).json()["error"]
    assert spanish["title"] == "El registro no existe." and spanish["message"] == "no invoice 7"
    catalan = (await api.get("/missing")).json()["error"]
    assert catalan["title"] == "El registre no existeix."


async def test_the_users_locale_claim_beats_the_browser(tmp_path):
    api = client(tmp_path)
    headers = {"Accept-Language": "en", "Authorization": "Bearer es"}
    chosen = await api.get("/me", headers=headers)
    assert chosen.json() == {"locale": "es"} and chosen.headers["content-language"] == "es"
    browser = await api.get(
        "/me", headers={"Accept-Language": "en", "Authorization": "Bearer none"}
    )
    assert browser.json() == {"locale": "en"}
    unknown = await api.get("/me", headers={"Accept-Language": "en", "Authorization": "Bearer ja"})
    assert unknown.json() == {"locale": "en"}  # not offered: the browser's stays
