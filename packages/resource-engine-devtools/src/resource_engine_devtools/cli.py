"""``resource-engine`` command line. Every command accepts ``--json`` for agents and CI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from resource_engine import ResourceRegistry
from resource_engine.application.hooks import HookPoint

from resource_engine_devtools.check import check_domain_imports
from resource_engine_devtools.documentation import introspect, validate
from resource_engine_devtools.documentation.generators import (
    render_ai_context,
    render_json,
    render_markdown,
)
from resource_engine_devtools.loader import load_authz, load_registry


def _emit(args: argparse.Namespace, data: Any, text: str) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str) if args.json else text)


def _registry(args: argparse.Namespace) -> ResourceRegistry:
    return load_registry(args.app, Path.cwd())


def _describe(registry: ResourceRegistry, name: str | None) -> list[dict[str, Any]]:
    from resource_engine.authz.catalog import Catalog

    out = []
    for d in registry:
        if name and d.name != name:
            continue
        hooks: dict[str, list[str]] = {}
        for point in HookPoint:  # contractual order
            for key, fns in d.hooks.items():
                if key.point is point:
                    hooks.setdefault(str(key), []).extend(
                        f"{fn.__module__}.{fn.__qualname__}" for fn in fns
                    )
        out.append(
            {
                "name": d.name,
                "resource": f"{d.resource_cls.__module__}.{d.resource_cls.__qualname__}",
                "model": f"{d.model.__module__}.{d.model.__qualname__}",
                "id": {"field": d.id_field, "type": d.id_type.__name__},
                "operations": list(d.operations),
                "schemas": {role: s.__name__ for role, s in d.schemas.items()},
                "filterable": list(d.filterable),
                "sortable": list(d.sortable),
                "state_machine": None
                if d.machine is None
                else {
                    "class": d.state_machine.__qualname__,  # type: ignore[union-attr]
                    "field": d.state_field,
                    "initial": d.machine.info.initial.id if d.machine.info.initial else None,
                    "transitions": [
                        {
                            "event": t.event,
                            "source": t.source,
                            "target": t.target,
                            "input": getattr(d.transition_schemas.get(t.event), "__name__", None),
                        }
                        for t in d.machine.info.transitions
                    ],
                },
                "actions": [
                    {
                        "name": a.name,
                        "instance": a.instance,
                        "input": getattr(a.input_schema, "__name__", None),
                        "output": getattr(a.output_schema, "__name__", None),
                    }
                    for a in d.actions.values()
                ],
                "permissions": [
                    a.name
                    for a in Catalog.from_registry(registry).actions.values()
                    if a.resource == d.name and a.kind not in ("read", "write")
                ],
                "restricted_fields": list(d.restricted_fields),
                "audit": {
                    "enabled": getattr(d.resource_cls, "audit", True),
                    "reads": getattr(d.resource_cls, "audit_reads", False),
                    "exclude": list(getattr(d.resource_cls, "audit_exclude", ())),
                },
                "documents": [
                    {
                        "slot": s.name,
                        "content_types": list(s.content_types),
                        "max_bytes": s.max_bytes,
                        "multiple": s.multiple,
                        "upload": s.upload,
                        "required_for": list(s.required_for),
                        "processing": s.processing,
                    }
                    for s in d.document_slots.values()
                ],
                "hooks": hooks,
            }
        )
    if name and not out:
        raise SystemExit(f"Resource {name!r} is not registered")
    return out


def cmd_inspect(args: argparse.Namespace) -> int:
    data = _describe(_registry(args), args.resource)
    lines = []
    for r in data:
        lines.append(f"{r['name']}  ({r['resource']})")
        lines.append(f"  operations: {', '.join(r['operations']) or '-'}")
        lines.append(f"  schemas:    {r['schemas']}")
        if r["state_machine"]:
            sm = r["state_machine"]
            lines.append(
                f"  state:      {sm['class']} on '{sm['field']}' (initial {sm['initial']})"
            )
            for t in sm["transitions"]:
                lines.append(f"    {t['source']} --{t['event']}--> {t['target']}")
        for a in r["actions"]:
            lines.append(f"  action:     {a['name']} (input {a['input']})")
        lines.append(f"  permissions: {', '.join(r['permissions'])}")
        if r["restricted_fields"]:
            lines.append(f"  restricted fields: {', '.join(r['restricted_fields'])}")
        audit = r["audit"]
        if not audit["enabled"] or audit["reads"] or audit["exclude"]:
            parts = ["off"] if not audit["enabled"] else []
            parts += ["reads"] if audit["reads"] else []
            parts += [f"exclude {', '.join(audit['exclude'])}"] if audit["exclude"] else []
            lines.append(f"  audit:      {'; '.join(parts)}")
        for s in r["documents"]:
            required = f", required for {'/'.join(s['required_for'])}" if s["required_for"] else ""
            lines.append(f"  document:   {s['slot']} ({', '.join(s['content_types'])}{required})")
        for point, fns in r["hooks"].items():
            lines.append(f"  hook {point}: {', '.join(fns)}")
    _emit(args, data, "\n".join(lines))
    return 0


def cmd_docs_generate(args: argparse.Namespace) -> int:
    model = introspect(_registry(args), load_authz(Path.cwd()))
    out = Path(args.out)
    if args.format == "json":
        files = {"documentation.json": render_json(model)}
    else:
        files = render_markdown(model)
    for rel, content in files.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _emit(
        args,
        {"written": sorted(str(out / f) for f in files)},
        "\n".join(f"wrote {out / f}" for f in sorted(files)),
    )
    return 0


def cmd_docs_validate(args: argparse.Namespace) -> int:
    findings = validate(introspect(_registry(args), load_authz(Path.cwd())))
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    failed = bool(errors) or (args.strict and bool(warnings))
    labels = {"ok": "OK  ", "warning": "WARN", "error": "ERR "}
    text = (
        "Documentation validation\n\n"
        + "\n".join(
            f"{labels[f.level]} {f.code} {f.target}: {f.message}"
            for f in findings
            if args.verbose or f.level != "ok"
        )
        + f"\n\n{len(errors)} errors, {len(warnings)} warnings"
    )
    _emit(args, {"passed": not failed, "findings": [f.to_dict() for f in findings]}, text)
    return 1 if failed else 0


def cmd_ai_context(args: argparse.Namespace) -> int:
    content = render_ai_context(introspect(_registry(args), load_authz(Path.cwd())))
    if not args.out:
        print(content, end="")
        return 0
    target = Path(args.out)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else None
        up_to_date = current == content
        _emit(
            args,
            {"up_to_date": up_to_date, "path": str(target)},
            f"{target}: up to date"
            if up_to_date
            else f"{target}: OUT OF DATE (run resource-engine ai context --out {target})",
        )
        return 0 if up_to_date else 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _emit(args, {"written": str(target)}, f"wrote {target}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    violations = []
    for raw in args.paths or ["."]:
        violations += check_domain_imports(Path(raw))
    text = (
        "\n".join(f"{v.code} {v.path}:{v.line}: {v.message}\n    {v.hint}" for v in violations)
        or "No violations."
    )
    _emit(args, {"passed": not violations, "violations": [v.to_dict() for v in violations]}, text)
    return 1 if violations else 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--app", help="registry as module:attribute (default: pyproject config)")

    parser = argparse.ArgumentParser(prog="resource-engine", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", parents=[common], help="show the resolved resource definitions")
    p.add_argument("resource", nargs="?")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("check", parents=[common], help="architecture lints")
    p.add_argument("paths", nargs="*")
    p.set_defaults(func=cmd_check)

    docs = sub.add_parser("docs", help="functional documentation").add_subparsers(
        dest="docs_command", required=True
    )
    p = docs.add_parser("generate", parents=[common])
    p.add_argument("--out", default="docs/functional")
    p.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p.set_defaults(func=cmd_docs_generate)
    p = docs.add_parser("validate", parents=[common])
    p.add_argument("--strict", action="store_true", help="fail on warnings too")
    p.add_argument("-v", "--verbose", action="store_true", help="also list passing checks")
    p.set_defaults(func=cmd_docs_validate)

    ai = sub.add_parser("ai", help="context for AI coding agents").add_subparsers(
        dest="ai_command", required=True
    )
    p = ai.add_parser("context", parents=[common], help="generate the project catalogue")
    p.add_argument("--out")
    p.add_argument("--check", action="store_true", help="fail if --out is out of date")
    p.set_defaults(func=cmd_ai_context)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
