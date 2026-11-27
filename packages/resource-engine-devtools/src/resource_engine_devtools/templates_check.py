"""``check`` RE-C007: an ``EmailRequested`` names a template set the
application's renderer doesn't have.

The renderer is ``[tool.resource-engine] templates = "pkg.module:templates"``.
Each ``EmailRequested(template="...")`` with a literal name (found by
``resource_engine_devtools.emails``) needs a subject and a text or html body,
for its literal ``locale`` or, without one, unlocalised. Requests built with a
computed name can't be checked here.
"""

from __future__ import annotations

from typing import Any

from resource_engine import ResourceRegistry

from resource_engine_devtools.check import Violation
from resource_engine_devtools.emails import find_email_requests


def check_templates(
    registry: ResourceRegistry, renderer: Any, *, prefix: str = "email/"
) -> list[Violation]:
    violations: list[Violation] = []
    seen: set[tuple[str, int]] = set()
    for use in find_email_requests(registry):
        if use.template is None or (use.path, use.line) in seen:
            continue
        seen.add((use.path, use.line))
        missing = missing_template(renderer, prefix, use.template, use.locale)
        if missing:
            violations.append(
                Violation(
                    code="RE-C007",
                    path=use.path,
                    line=use.line,
                    message=f"{use.resource} {use.point} requests the email {use.template!r}"
                    + (f" in {use.locale!r}" if use.locale else "")
                    + f", but the templates lack {missing}",
                    hint=f"Add {prefix}{use.template}.subject.txt and "
                    f"{prefix}{use.template}.txt or .html to the templates folder.",
                )
            )
    return violations


def missing_template(renderer: Any, prefix: str, template: str, locale: str | None) -> str | None:
    base = f"{prefix}{template}"
    subject = f"{base}.subject.txt"
    if not renderer.exists(subject, locale=locale):
        return subject
    if not (
        renderer.exists(f"{base}.txt", locale=locale)
        or renderer.exists(f"{base}.html", locale=locale)
    ):
        return f"{base}.txt or {base}.html"
    return None
