"""The language of each request: ``Accept-Language`` negotiated against the
application's locales, or the signed-in user's ``locale`` claim.

```python
from resource_engine.i18n import Translations, set_translations
from resource_engine_fastapi import install_locale

translations = Translations("invoices/locale", default_locale="ca")
set_translations(translations)
install_locale(app, translations)
```

Inside the request, ``resource_engine.i18n.gettext`` and error titles
(``{"error": {"title": ...}}``) use that locale, and the answer carries
``Content-Language``. JSON data is never translated: front ends translate
their own texts (``resource-engine i18n export``) and show error titles or
their own text per ``code``.
"""

from __future__ import annotations

from typing import Any

from resource_engine.i18n import Translations, get_locale, set_locale, use_locale


class LocaleMiddleware:
    """ASGI middleware: picks the request's locale and sets ``Content-Language``."""

    def __init__(self, app: Any, translations: Translations) -> None:
        self.app = app
        self.translations = translations

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        locale = (
            self.translations.negotiate(headers.get("accept-language"))
            or self.translations.default_locale
        )

        async def send_with_language(message: Any) -> None:
            if message["type"] == "http.response.start":
                current = get_locale()
                if current:
                    message.setdefault("headers", [])
                    message["headers"] = [
                        *message["headers"],
                        (b"content-language", current.encode()),
                    ]
            await send(message)

        with use_locale(locale):
            await self.app(scope, receive, send_with_language)


def install_locale(app: Any, translations: Translations) -> None:
    """Negotiate each request's locale (see ``LocaleMiddleware``)."""
    app.add_middleware(LocaleMiddleware, translations=translations)


def principal_locale(principal: Any, translations: Translations) -> None:
    """Use the signed-in user's ``locale`` claim (OIDC standard claim) when it is
    one of the offered locales: a profile setting beats the browser's."""
    claims = getattr(principal, "claims", None) or {}
    wanted = claims.get("locale")
    if isinstance(wanted, str) and wanted:
        chosen = translations.negotiate(wanted)
        if chosen:
            set_locale(chosen)


__all__ = ["LocaleMiddleware", "install_locale", "principal_locale"]
