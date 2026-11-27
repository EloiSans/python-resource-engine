"""A browsable static site of the functional documentation: the same pages as
``docs generate`` (resources, state diagrams, permission matrix) plus the
published events, as plain HTML with navigation and search.

``resource-engine docs site --out site/`` writes it; any static host serves it
(GitHub Pages, S3, an intranet server). Diagrams are drawn in the browser by
Mermaid, from a CDN unless ``--mermaid-url`` points elsewhere (or is empty:
then they stay as text).
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from markdown_it import MarkdownIt

from resource_engine_devtools.documentation.generators.markdown import render_markdown
from resource_engine_devtools.documentation.i18n import docs_locale, t, translate_model
from resource_engine_devtools.documentation.model import DocumentationModel

MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs"


def labels() -> dict[str, str]:
    """The site's own texts, in the current docs locale."""
    return {
        "home": t("Home"),
        "permissions": t("Permissions"),
        "events": t("Events"),
        "machines": t("State machines"),
        "emails": t("Emails"),
        "resources": t("Resources"),
        "search": t("Search…"),
        "no_results": t("No results"),
        "generated": t("Generated from the code with Resource Engine"),
        "language": t("Language"),
    }


def render_site(
    model: DocumentationModel,
    *,
    asyncapi: Mapping[str, Any] | None = None,
    title: str | None = None,
    mermaid_url: str = MERMAID_URL,
    locales: Sequence[str] | None = None,
    translate: Callable[[str], Callable[[str], str]] | None = None,
) -> dict[str, str]:
    """Return ``{relative_path: content}`` of the site.

    With several ``locales`` each gets a copy of the site under its own folder
    (``ca/``, ``en/``), a language switcher on every page, and a root
    ``index.html`` that sends visitors to the first one. ``translate(locale)``
    gives the application's gettext for a locale (its docstrings and
    descriptions); the generator's own texts come with devtools.
    """
    if locales and len(locales) > 1:
        files: dict[str, str] = {}
        for locale in locales:
            localised = translate_model(model, translate(locale)) if translate else model
            with docs_locale(locale):
                part = _render_site(
                    localised, asyncapi, title, mermaid_url, locale=locale, others=locales
                )
            files.update({f"{locale}/{path}": text for path, text in part.items()})
        files["index.html"] = _redirect(list(locales))
        return files
    locale = locales[0] if locales else None
    if locale and translate:
        model = translate_model(model, translate(locale))
    with docs_locale(locale):
        return _render_site(model, asyncapi, title, mermaid_url, locale=locale, others=())


def _render_site(
    model: DocumentationModel,
    asyncapi: Mapping[str, Any] | None,
    title: str | None,
    mermaid_url: str,
    *,
    locale: str | None,
    others: Sequence[str],
) -> dict[str, str]:
    LABELS = labels()
    pages = render_markdown(model)
    if asyncapi and asyncapi.get("channels"):
        pages["events.md"] = render_events(asyncapi)
    title = title or (asyncapi or {}).get("info", {}).get("title") or t("Functional documentation")
    md = _markdown()
    nav = _navigation(
        model,
        "permissions.md" in pages,
        "events.md" in pages,
        "state-machines.md" in pages,
        "emails.md" in pages,
    )
    files: dict[str, str] = {}
    search: list[dict[str, str]] = []
    for path, text in sorted(pages.items()):
        target = path.removesuffix(".md") + ".html"
        heading = _first_heading(text) or path
        body = md.render(text)
        files[target] = _page(
            title=title,
            heading=heading,
            body=_links(body),
            nav=nav,
            current=target,
            root="../" * target.count("/"),
            mermaid_url=mermaid_url,
            locale=locale,
            others=others,
            labels=LABELS,
        )
        search.append({"path": target, "title": heading, "text": _plain(text)[:4000]})
    files["assets/site.css"] = _asset("site.css")
    index = json.dumps(search, ensure_ascii=False)
    script = _asset("site.js").replace("__NO_RESULTS__", json.dumps(LABELS["no_results"]))
    files["assets/site.js"] = f"window.SITE_SEARCH = {index};\n{script}"
    return files


def render_events(asyncapi: Mapping[str, Any]) -> str:
    """The published events (AsyncAPI) as a page: one section per channel."""
    components = asyncapi.get("components", {})
    messages = components.get("messages", {})
    schemas = components.get("schemas", {})
    out = [
        f"# {t('Published events')}",
        "",
        t(
            "Messages the application publishes after each committed change. The complete "
            "AsyncAPI document is `asyncapi.json`."
        ),
    ]
    for name, channel in asyncapi.get("channels", {}).items():
        out += ["", f"## {name}", ""]
        if channel.get("description"):
            out += [channel["description"].strip().splitlines()[0], ""]
        out += [f"| {t('Event')} | {t('Description')} | {t('Data')} |", "|---|---|---|"]
        for event, ref in channel.get("messages", {}).items():
            message = messages.get(_ref_name(ref), {})
            schema = schemas.get(_ref_name(message.get("payload", {})), {})
            payload = schema.get("properties", {}).get("payload", {}).get("properties", {})
            fields = ", ".join(f"`{f}`{_describe(s)}" for f, s in payload.items()) or "—"
            summary = _cell(message.get("summary") or "")
            out.append(f"| `{event}` | {summary or '—'} | {fields} |")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------- helpers


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    default_fence = md.renderer.rules.get("fence")

    def fence(tokens: Any, idx: int, options: Any, env: Any) -> str:
        token = tokens[idx]
        if token.info.strip() == "mermaid":
            return f'<pre class="mermaid">{html.escape(token.content)}</pre>\n'
        if token.info.strip() == "email-html":
            # the email's own HTML, isolated: no scripts, no access to the site
            return (
                f'<iframe class="email-preview" sandbox="" referrerpolicy="no-referrer" '
                f'loading="lazy" title="HTML" srcdoc="{html.escape(token.content, quote=True)}">'
                "</iframe>\n"
            )
        return default_fence(tokens, idx, options, env)

    def heading_open(tokens: Any, idx: int, options: Any, env: Any) -> str:
        token = tokens[idx]
        slug = _slug(tokens[idx + 1].content)
        seen = env.setdefault("slugs", {})
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        return f'<{token.tag} id="{slug}">'

    md.renderer.rules["fence"] = fence
    md.renderer.rules["heading_open"] = heading_open
    return md


def _slug(text: str) -> str:
    text = re.sub(r"[`*\[\]()]", "", text.lower())
    return re.sub(r"[^\w]+", "-", text).strip("-") or "section"


def _links(body: str) -> str:
    """Links between the Markdown pages point at their HTML pages."""
    return re.sub(r'href="([^":#]+)\.md(#[^"]*)?"', r'href="\1.html\2"', body)


def _first_heading(text: str) -> str | None:
    match = re.search(r"^# (.+)$", text, re.MULTILINE)
    return match.group(1).strip().strip("`") if match else None


def _plain(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"[`#*|\[\]()>_-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _ref_name(ref: Any) -> str:
    value = ref.get("$ref", "") if isinstance(ref, Mapping) else ""
    return value.rsplit("/", 1)[-1]


def _describe(schema: Mapping[str, Any]) -> str:
    if "const" in schema:
        return f" = `{schema['const']}`"
    if "enum" in schema:
        values = ", ".join(str(v) for v in schema["enum"][:8])
        return f" ({values}{'…' if len(schema['enum']) > 8 else ''})"
    return ""


def _navigation(
    model: DocumentationModel, permissions: bool, events: bool, machines: bool, emails: bool
) -> list[tuple]:
    LABELS = labels()
    items: list[tuple] = [("index.html", LABELS["home"], None)]
    if machines:
        items.append(("state-machines.html", LABELS["machines"], None))
    if emails:
        items.append(("emails.html", LABELS["emails"], None))
    if permissions:
        items.append(("permissions.html", LABELS["permissions"], None))
    if events:
        items.append(("events.html", LABELS["events"], None))
    resources = [(f"resources/{r.name}.html", r.name, r.doc.summary or "") for r in model.resources]
    return [*items, ("", LABELS["resources"], resources)]


def _page(
    *,
    title: str,
    heading: str,
    body: str,
    nav: list[tuple],
    current: str,
    root: str,
    mermaid_url: str,
    locale: str | None = None,
    others: Sequence[str] = (),
    labels: Mapping[str, str],
) -> str:
    LABELS = labels
    links = []
    for href, label, children in nav:
        if children is None:
            active = ' aria-current="page"' if href == current else ""
            links.append(f'<li><a href="{root}{href}"{active}>{html.escape(label)}</a></li>')
            continue
        links.append(f'<li class="group">{html.escape(label)}<ul>')
        for child, name, summary in children:
            active = ' aria-current="page"' if child == current else ""
            tip = f' title="{html.escape(summary)}"' if summary else ""
            links.append(f'<li><a href="{root}{child}"{active}{tip}>{html.escape(name)}</a></li>')
        links.append("</ul></li>")
    mermaid = (
        f'<script type="module">import mermaid from {json.dumps(mermaid_url)};'
        "mermaid.initialize({startOnLoad: true, flowchart: {useMaxWidth: false}, theme: matchMedia("
        "'(prefers-color-scheme: dark)').matches ? 'dark' : 'default'});</script>"
        if mermaid_url and '<pre class="mermaid">' in body
        else ""
    )
    return f"""<!doctype html>
<html lang="{html.escape(locale or "en")}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(heading)} · {html.escape(title)}</title>
<link rel="stylesheet" href="{root}assets/site.css">
</head>
<body data-root="{root}">
<header><button id="menu-toggle" aria-controls="nav" aria-expanded="false">☰</button>
<a class="brand" href="{root}index.html">{html.escape(title)}</a>
<input id="search" type="search" placeholder="{LABELS["search"]}" aria-label="{LABELS["search"]}">
{_switcher(locale, others, current, root, LABELS["language"])}
</header>
<div class="layout">
<nav id="nav" aria-label="{LABELS["resources"]}">
<ul id="results" hidden></ul>
<ul id="menu">{"".join(links)}</ul>
</nav>
<main>{body}</main>
</div>
<footer>{LABELS["generated"]}</footer>
<script src="{root}assets/site.js"></script>
{mermaid}
</body>
</html>
"""


def _switcher(
    locale: str | None, others: Sequence[str], current: str, root: str, label: str
) -> str:
    """Links to the same page in the other languages (``../en/resources/x.html``)."""
    if len(others) < 2:
        return ""
    links = []
    for other in others:
        if other == locale:
            links.append(f'<span aria-current="true" lang="{other}">{html.escape(other)}</span>')
        else:
            links.append(
                f'<a href="{root}../{other}/{current}" hreflang="{other}" lang="{other}">'
                f"{html.escape(other)}</a>"
            )
    return f'<nav class="languages" aria-label="{html.escape(label)}">{" ".join(links)}</nav>'


def _redirect(locales: list[str]) -> str:
    """The root page: the visitor's language when offered, else the first one."""
    target = f"{locales[0]}/index.html"
    script = (
        f"var offered = {json.dumps(locales)};"
        "var wanted = (navigator.languages || [navigator.language || '']).map("
        "function (l) { return l.toLowerCase(); });"
        "var pick = offered[0];"
        "outer: for (var i = 0; i < wanted.length; i++) {"
        " for (var j = 0; j < offered.length; j++) {"
        "  var o = offered[j].toLowerCase();"
        "  if (wanted[i] === o || wanted[i].split('-')[0] === o.split('-')[0]) {"
        "   pick = offered[j]; break outer; } } }"
        "location.replace(pick + '/index.html');"
    )
    links = " · ".join(f'<a href="{loc}/index.html" hreflang="{loc}">{loc}</a>' for loc in locales)
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<noscript><meta http-equiv="refresh" content="0; url={target}"></noscript>
<script>{script}</script>
</head><body><p>{links}</p></body></html>
"""


def _asset(name: str) -> str:
    from importlib.resources import files

    return (files(__package__) / "site_assets" / name).read_text(encoding="utf-8")


__all__ = ["MERMAID_URL", "labels", "render_events", "render_site"]
