"""The explorer's texts, in English with Catalan and Spanish catalogues
(``explorer/locale/<locale>/LC_MESSAGES/explorer.mo``, compiled from the
``.po`` files next to them). Shared by the standalone explorer and the
documentation site's console."""

from __future__ import annotations

import gettext
from functools import cache
from importlib.resources import files

from resource_engine.templates import locale_candidates

DOMAIN = "explorer"

#: Every text, by key: the English message is the catalogue's id.
LABELS: dict[str, str] = {
    # operations (the try-it console)
    "try_it": "Try it",
    "intro": "Calls the running API with your token. Reads only, unless you allow changes.",
    "token": "Access token or API key",
    "token_hint": "Kept in this browser tab only",
    "allow_changes": "Allow changes (create, update, delete, transitions, actions)",
    "changes_off": "This operation changes data: tick “Allow changes” first.",
    "operation": "Operation",
    "header": "header",
    "body": "Body (JSON)",
    "send": "Send",
    "send_change": "Send the change",
    "sending": "Sending…",
    "missing": "Fill in",
    "bad_json": "The body is not valid JSON",
    "unreachable": "The API could not be reached",
    "why": "Why was it refused?",
    "next": "Allowed transitions now",
    "none": "none",
    "upload_note": "Files are uploaded with the multipart form; use another client.",
    "who_am_i": "What can I do?",
    "who_intro": "Your roles and the actions you may perform, from the running API.",
    "explain_editors": (
        "Only policy editors can see the full explanation. What the API says about you:"
    ),
    "your_roles": "Your roles",
    "not_granted": "not granted",
    "download": "Download",
    "export_check": "Check the export",
    # the standalone page
    "explorer": "Explorer",
    "tab_operations": "Operations",
    "tab_events": "Events",
    "resource": "Resource",
    # events
    "events_intro": (
        "Events committed from now on that you may see: those about records you can read. "
        "Payloads and events about no record need the events:read permission."
    ),
    "connect": "Connect",
    "disconnect": "Disconnect",
    "pause": "Pause",
    "resume": "Resume",
    "clear": "Clear",
    "filter_resource": "Resource (channel)",
    "filter_type": "Event type",
    "filter_version": "Schema version",
    "filter_id": "Record id",
    "any": "any",
    "connected": "Connected: waiting for events…",
    "disconnected": "Disconnected",
    "no_events": "No events yet.",
    "envelope": "Envelope",
    "record": "Record as you can read it",
    "payload": "Payload",
    "payload_hidden": "The payload needs the events:read permission.",
    "deliveries": "Handled by",
    "no_deliveries": "No consumer has reported handling it yet.",
    "deliveries_off": "Consumer results are not recorded (no activity log).",
}


@cache
def _translation(locale: str) -> gettext.NullTranslations:
    directory = files(__package__) / "locale"
    return gettext.translation(DOMAIN, str(directory), languages=[locale], fallback=True)


def labels(locale: str | None = None) -> dict[str, str]:
    """Every text in ``locale`` (``ca-ES`` falls back to ``ca``, then English)."""
    out = {}
    candidates = [c for c in locale_candidates(locale) if c] if locale else []
    for key, message in LABELS.items():
        text = message
        for candidate in candidates:
            found = _translation(candidate).gettext(message)
            if found != message:
                text = found
                break
        out[key] = text
    return out


def locales() -> list[str]:
    """Languages the explorer is translated into (plus English)."""
    root = files(__package__) / "locale"
    found = [p.name for p in root.iterdir() if (p / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file()]
    return sorted({"en", *found})


__all__ = ["DOMAIN", "LABELS", "labels", "locales"]
