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
from resource_engine_devtools.loader import find_config, load_authz, load_registry


def _emit(args: argparse.Namespace, data: Any, text: str) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str) if args.json else text)


def _registry(args: argparse.Namespace) -> ResourceRegistry:
    return load_registry(args.app, Path.cwd())


def _optional_registry(args: argparse.Namespace) -> ResourceRegistry | None:
    """The registry when one is configured; hook lints are skipped without it."""
    try:
        return _registry(args)
    except SystemExit:
        if args.app:
            raise
        return None


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
                "tenant_field": d.tenant_field,
                "version_field": d.version_field,
                "require_version": d.require_version,
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
        if r["tenant_field"]:
            lines.append(f"  tenant field: {r['tenant_field']}")
        if r["version_field"]:
            strict = " (required)" if r["require_version"] else ""
            lines.append(f"  version field: {r['version_field']}{strict}")
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


def _project_context(args: argparse.Namespace) -> str | None:
    registry = _optional_registry(args)
    if registry is None:
        return None
    return render_ai_context(introspect(registry, load_authz(Path.cwd())))


def cmd_ai_install(args: argparse.Namespace) -> int:
    from resource_engine_devtools.ai import installer

    targets = installer.parse_targets(args.target)
    result = installer.install(Path(args.root), targets, _project_context(args))
    lines = [f"installed resource-engine skill {result['version']} → {result['skill_path']}"]
    lines += [f"  wrote {name}" for name in result["written"]]
    lines += [f"  removed {name}" for name in result["removed"]]
    _emit(args, result, "\n".join(lines))
    return 0


def cmd_ai_sync(args: argparse.Namespace) -> int:
    from resource_engine_devtools.ai import installer

    root = Path(args.root)
    context = _project_context(args)
    lock, findings = installer.check(root, context)
    if args.check or lock is None:
        up_to_date = not findings
        text = (
            "AI skill and project context are up to date."
            if up_to_date
            else (
                installer.summary(findings) + "\nRun: resource-engine ai sync"
                if lock is not None
                else installer.summary(findings)
            )
        )
        _emit(args, {"up_to_date": up_to_date, "findings": [f.to_dict() for f in findings]}, text)
        return 0 if up_to_date else 1
    result = installer.install(root, lock["targets"], context)
    lines = [f"synced resource-engine skill {result['version']} → {result['skill_path']}"]
    lines += [f"  wrote {name}" for name in result["written"]] or ["  nothing to update"]
    lines += [f"  removed {name}" for name in result["removed"]]
    _emit(args, result | {"findings": [f.to_dict() for f in findings]}, "\n".join(lines))
    return 0


def cmd_db_init(args: argparse.Namespace) -> int:
    from resource_engine_devtools import db

    config = find_config(Path.cwd())
    metadata = args.metadata or config.get("metadata")
    if not metadata:
        raise SystemExit(
            "No metadata given. Pass --metadata pkg.persistence:metadata or set "
            '[tool.resource-engine] metadata = "pkg.persistence:metadata" in pyproject.toml'
        )
    sys_path = [str(p) for p in config.get("paths", [])] or ["."]  # type: ignore[union-attr]
    written = db.init(
        Path.cwd(), str(metadata), directory=args.dir, sys_path=sys_path, force=args.force
    )
    lines = [f"wrote {path}" for path in written]
    lines += [
        "",
        "Next:",
        '  alembic revision --autogenerate -m "initial schema"',
        "  alembic upgrade head   # with DATABASE_URL set",
        "  alembic check          # in CI: fails when the code has changes without a migration",
    ]
    _emit(args, {"written": [str(p) for p in written]}, "\n".join(lines))
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    from resource_engine_devtools import scaffold

    root = Path(args.root) if args.root else _default_root()
    try:
        fields = scaffold.parse_fields(getattr(args, "field", None))
        if args.scaffold_command == "resource":
            machine = (
                scaffold.parse_machine(args.states, args.transitions)
                if args.state_machine or args.states or args.transitions
                else None
            )
            result = scaffold.resource(
                root,
                args.package,
                args.name,
                fields,
                entity=args.entity,
                machine=machine,
                versioned=args.versioned,
                force=args.force,
                dry_run=args.dry_run,
            )
        elif args.scaffold_command == "action":
            result = scaffold.action(
                root,
                args.package,
                args.resource,
                args.action_class,
                fields,
                collection=args.collection,
                dry_run=args.dry_run,
            )
        else:
            states, transitions = scaffold.parse_machine(args.states, args.transitions)
            result = scaffold.state_machine(
                root,
                args.package,
                args.entity,
                states,
                transitions,
                force=args.force,
                dry_run=args.dry_run,
            )
    except scaffold.ScaffoldError as exc:
        _emit(args, {"error": exc.message}, f"error: {exc.message}")
        return 2
    verb = "would write" if args.dry_run else "wrote"
    lines = [f"{verb} {path}" for path in result.written]
    lines += [f"kept {path} (exists; --force to replace)" for path in result.skipped]
    lines += ["", "Next:"] + [f"  - {step}" for step in result.next_steps]
    _emit(args, result.to_dict() | {"dry_run": args.dry_run}, "\n".join(lines))
    return 0


def _default_root() -> Path:
    config = find_config(Path.cwd())
    paths = config.get("paths") or []
    if paths:
        return Path(str(config["_root"])) / str(paths[0])  # type: ignore[index]
    return Path.cwd()


def cmd_check(args: argparse.Namespace) -> int:
    violations = []
    for raw in args.paths or ["."]:
        violations += check_domain_imports(Path(raw))
    from resource_engine.errors import ResourceEngineError

    from resource_engine_devtools import gates_check
    from resource_engine_devtools.hooks_check import check_hooks

    try:
        registry = _optional_registry(args)
    except ResourceEngineError as exc:  # the engine refused a declaration
        registry = None
        violations.append(gates_check.load_error(exc))
    if registry is not None:
        violations += check_hooks(registry)
        seed = args.policies or find_config(Path.cwd()).get("policies")
        if seed:
            violations += gates_check.check_gates(
                registry, load_authz(Path.cwd()), gates_check.load_policies(seed)
            )
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

    p = sub.add_parser("check", parents=[common], help="architecture, hook/effect and gate lints")
    p.add_argument("paths", nargs="*")
    p.add_argument(
        "--policies",
        help="seed policies as module:attribute (default: pyproject policies); "
        "enables the operations-without-a-gate rule",
    )
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
    p = ai.add_parser("install", parents=[common], help="install the skill and agent instructions")
    p.add_argument(
        "--target",
        help="comma-separated: claude, agents, cursor, copilot (default: claude,agents)",
    )
    p.add_argument("--root", default=".", help="project root (default: current directory)")
    p.set_defaults(func=cmd_ai_install)
    p = ai.add_parser(
        "sync", parents=[common], help="update the installed skill and project context"
    )
    p.add_argument("--check", action="store_true", help="fail if anything is out of date")
    p.add_argument("--root", default=".", help="project root (default: current directory)")
    p.set_defaults(func=cmd_ai_sync)

    db = sub.add_parser("db", help="database migrations (Alembic)").add_subparsers(
        dest="db_command", required=True
    )
    p = db.add_parser("init", help="write alembic.ini and migrations/ for your MetaData")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--metadata", help="MetaData as module:attribute (default: pyproject metadata)")
    p.add_argument("--dir", default="migrations", help="migrations folder (default: migrations)")
    p.add_argument("--force", action="store_true", help="replace existing files")
    p.set_defaults(func=cmd_db_init)

    scaffold = sub.add_parser(
        "scaffold", help="generate the boilerplate of a module, action or state machine"
    ).add_subparsers(dest="scaffold_command", required=True)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--json", action="store_true", help="machine-readable output")
    shared.add_argument("--package", required=True, help="dotted package of the module")
    shared.add_argument(
        "--root", help="directory the package lives in (default: first pyproject path, or .)"
    )
    shared.add_argument("--dry-run", action="store_true", help="show what would be written")
    p = scaffold.add_parser("resource", parents=[shared], help="a new module with one resource")
    p.add_argument("name", help="resource name, plural snake_case (orders)")
    p.add_argument("--entity", help="entity class (default: singular PascalCase of the name)")
    p.add_argument(
        "--field",
        action="append",
        help="name:type[?], type in str text int bool Decimal date "
        "datetime; ? makes it optional (repeatable)",
    )
    p.add_argument("--state-machine", action="store_true", help="add a state machine")
    p.add_argument("--states", help="comma-separated states (default: draft,active,closed)")
    p.add_argument("--transitions", help="event:source>target,... (default: one per step)")
    p.add_argument("--versioned", action="store_true", help="optimistic concurrency")
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.set_defaults(func=cmd_scaffold)
    p = scaffold.add_parser("action", parents=[shared], help="an action in an existing module")
    p.add_argument("resource", help="resource name (for the permission and the route)")
    p.add_argument("action_class", metavar="ActionClass")
    p.add_argument("--input", action="append", dest="field", help="input field name:type[?]")
    p.add_argument("--collection", action="store_true", help="not tied to one record")
    p.set_defaults(func=cmd_scaffold)
    p = scaffold.add_parser(
        "state-machine", parents=[shared], help="a state machine in an existing module"
    )
    p.add_argument("entity", help="entity class name (Order)")
    p.add_argument("--states", help="comma-separated states (default: draft,active,closed)")
    p.add_argument("--transitions", help="event:source>target,... (default: one per step)")
    p.add_argument("--force", action="store_true", help="overwrite an existing state machine")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("worker", help="run the application's background jobs and services")
    p.add_argument("--runner", help="JobRunner as module:attribute (default: pyproject worker)")
    p.add_argument("--once", action="store_true", help="run every job once and exit")
    p.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    p.set_defaults(func=_worker)
    return parser


def _worker(args: argparse.Namespace) -> int:
    from resource_engine_devtools.worker import cmd_worker

    return cmd_worker(args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
