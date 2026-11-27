"""``resource-engine mcp``: the development CLI as MCP tools for coding agents.

```json
{"mcpServers": {"resource-engine": {"command": "uv", "args": ["run", "resource-engine", "mcp"]}}}
```

- **Tools:** the project catalogue, ``inspect``, ``check``, ``docs validate``,
  the AsyncAPI document and its compatibility check, ``explain_error`` and the
  scaffold commands.
- **Fresh runs:** each project tool runs the CLI with ``--json`` in a new
  process in the project directory, so it sees the code as it is now (agents
  edit it between calls), and nothing the application prints can corrupt the
  stdio protocol.
- **Resources:** the AI skill's guides (``resource-engine://skill/...``) and the
  project catalogue.

Needs the ``mcp`` extra: ``pip install 'resource-engine-devtools[mcp]'``.
See docs/ai.md#mcp-server.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).parent / "ai" / "skill"
TIMEOUT = 180.0
MAX_OUTPUT = 200_000


@dataclass(frozen=True)
class DevTool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[DevServer, dict[str, Any]], Awaitable[dict[str, Any]]]
    read_only: bool = True


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


_STRINGS = {"type": "array", "items": {"type": "string"}}
_DRY = {"type": "boolean", "default": False, "description": "Show what would be written only"}
_PACKAGE = {"type": "string", "description": "Dotted package of the module (shop.orders)"}
_ROOT = {
    "type": "string",
    "description": "Directory the package lives in (default: first pyproject path)",
}


class DevServer:
    """The development MCP server for the project in ``project``."""

    def __init__(self, project: Path | None = None, *, python: str | None = None) -> None:
        self.project = (project or Path.cwd()).resolve()
        self.python = python or sys.executable
        self.tools = {t.name: t for t in TOOLS}

    # ------------------------------------------------------------ running

    async def cli(self, *argv: str) -> dict[str, Any]:
        """Run ``resource-engine <argv>`` in a fresh process; its exit code and
        output (parsed JSON when it is JSON)."""
        process = await asyncio.create_subprocess_exec(
            self.python,
            "-m",
            "resource_engine_devtools.cli",
            *argv,
            cwd=self.project,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), TIMEOUT)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(
                f"resource-engine {' '.join(argv)} took more than {TIMEOUT:.0f} s"
            ) from None
        text = out.decode("utf-8", "replace")[:MAX_OUTPUT]
        try:
            output: Any = json.loads(text)
        except ValueError:
            output = text
        result: dict[str, Any] = {"exit_code": process.returncode, "output": output}
        errors = err.decode("utf-8", "replace").strip()
        if errors:
            result["stderr"] = errors[-4000:]
        return result

    # ------------------------------------------------------------------ mcp

    def build(self) -> Any:
        import mcp_types as types
        from mcp.server import Server

        async def list_tools(ctx: Any, params: Any) -> Any:
            return types.ListToolsResult(tools=[self._tool(t) for t in self.tools.values()])

        async def call_tool(ctx: Any, params: Any) -> Any:
            return await self.call(params.name, params.arguments or {})

        async def list_resources(ctx: Any, params: Any) -> Any:
            return types.ListResourcesResult(resources=self.resources())

        async def read_resource(ctx: Any, params: Any) -> Any:
            from mcp.shared.exceptions import MCPError

            uri = str(params.uri)
            try:
                text, mime = await self.read(uri)
            except ValueError as exc:
                raise MCPError(types.INVALID_PARAMS, str(exc)) from None
            return types.ReadResourceResult(
                contents=[types.TextResourceContents(uri=uri, text=text, mime_type=mime)]
            )

        return Server(
            "resource-engine-dev",
            instructions=INSTRUCTIONS,
            on_list_tools=list_tools,
            on_call_tool=call_tool,
            on_list_resources=list_resources,
            on_read_resource=read_resource,
        )

    @staticmethod
    def _tool(tool: DevTool) -> Any:
        import mcp_types as types

        return types.Tool(
            name=tool.name,
            title=tool.title,
            description=tool.description,
            input_schema=tool.input_schema,
            annotations=types.ToolAnnotations(
                title=tool.title,
                read_only_hint=tool.read_only,
                destructive_hint=False,
                idempotent_hint=tool.read_only,
                open_world_hint=False,
            ),
        )

    async def call(self, name: str, arguments: Mapping[str, Any]) -> Any:
        import mcp_types as types

        tool = self.tools.get(name)
        if tool is None:
            return _error(f"Unknown tool {name!r}")
        unknown = set(arguments) - set(tool.input_schema["properties"])
        missing = set(tool.input_schema.get("required", ())) - set(arguments)
        if unknown or missing:
            return _error(
                f"Bad arguments for {name}: "
                + "; ".join(
                    part
                    for part in (
                        unknown and f"unknown {sorted(unknown)}",
                        missing and f"missing {sorted(missing)}",
                    )
                    if part
                )
            )
        try:
            result = await tool.run(self, dict(arguments))
        except Exception as exc:  # the tool's own failure, shown to the agent
            return _error(f"{type(exc).__name__}: {exc}")
        failed = result.get("exit_code") not in (0, 1, None) or (
            "stderr" in result and not result.get("output")
        )
        text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
        return types.CallToolResult(
            content=[types.TextContent(text=text)], structured_content=result, is_error=failed
        )

    def resources(self) -> list[Any]:
        import mcp_types as types

        items = [
            types.Resource(
                uri="resource-engine://project",
                name="project",
                title="Project catalogue",
                description="This project's resources, operations, transitions, actions, "
                "events, references and permissions (generated now).",
                mime_type="text/markdown",
            )
        ]
        for path in sorted(SKILL_DIR.rglob("*.md")):
            relative = path.relative_to(SKILL_DIR).as_posix()
            if relative.startswith("examples/"):
                continue
            first = path.read_text(encoding="utf-8").lstrip().splitlines()[:1]
            items.append(
                types.Resource(
                    uri=f"resource-engine://skill/{relative}",
                    name=relative,
                    title=(first[0].lstrip("# ").strip() if first else relative),
                    mime_type="text/markdown",
                )
            )
        return items

    async def read(self, uri: str) -> tuple[str, str]:
        if uri == "resource-engine://project":
            result = await self.cli("ai", "context")
            output = result["output"]
            return (output if isinstance(output, str) else json.dumps(output)), "text/markdown"
        prefix = "resource-engine://skill/"
        if uri.startswith(prefix):
            path = (SKILL_DIR / uri[len(prefix) :]).resolve()
            if SKILL_DIR.resolve() in path.parents and path.is_file():
                return path.read_text(encoding="utf-8"), "text/markdown"
        raise ValueError(f"Unknown resource {uri!r}")

    async def run_stdio(self) -> None:
        from mcp.server.stdio import stdio_server

        server = self.build()
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


def _error(message: str) -> Any:
    import mcp_types as types

    return types.CallToolResult(content=[types.TextContent(text=message)], is_error=True)


# ------------------------------------------------------------------- tools


async def _catalogue(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    return await server.cli("ai", "context")


async def _inspect(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    return await server.cli(
        "inspect", "--json", *([args["resource"]] if args.get("resource") else [])
    )


async def _check(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    return await server.cli("check", "--json", *args.get("paths", []))


async def _docs_validate(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    argv = ["docs", "validate", "--json"]
    if args.get("strict", True):
        argv.append("--strict")
    result = await server.cli(*argv)
    output = result.get("output")
    if isinstance(output, dict) and not args.get("verbose"):
        output["findings"] = [f for f in output.get("findings", []) if f.get("level") != "ok"]
    return result


async def _asyncapi(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("check_against"):
        return await server.cli("docs", "asyncapi", "--json", "--check", args["check_against"])
    return await server.cli("docs", "asyncapi")


async def _explain(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    return explain_error(args["code"])


def _scaffold_tail(args: dict[str, Any]) -> list[str]:
    argv = ["--json", "--package", args["package"]]
    if args.get("root"):
        argv += ["--root", args["root"]]
    if args.get("dry_run"):
        argv.append("--dry-run")
    return argv


async def _scaffold_resource(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    argv = ["scaffold", "resource", args["name"], *_scaffold_tail(args)]
    for value in args.get("fields", []):
        argv += ["--field", value]
    for key in ("entity", "states", "transitions"):
        if args.get(key):
            argv += [f"--{key}", args[key]]
    if args.get("state_machine"):
        argv.append("--state-machine")
    if args.get("versioned"):
        argv.append("--versioned")
    return await server.cli(*argv)


async def _scaffold_action(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    argv = ["scaffold", "action", args["resource"], args["action_class"], *_scaffold_tail(args)]
    for value in args.get("inputs", []):
        argv += ["--input", value]
    if args.get("collection"):
        argv.append("--collection")
    return await server.cli(*argv)


async def _scaffold_state_machine(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    argv = ["scaffold", "state-machine", args["entity"], *_scaffold_tail(args)]
    for key in ("states", "transitions"):
        if args.get(key):
            argv += [f"--{key}", args[key]]
    return await server.cli(*argv)


async def _scaffold_consumer(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
    argv = ["scaffold", "consumer", args["name"], "--asyncapi", args["asyncapi"]]
    argv += _scaffold_tail(args)
    for value in args.get("channels", []):
        argv += ["--channel", value]
    for value in args.get("messages", []):
        argv += ["--message", value]
    if args.get("type_prefix"):
        argv += ["--type-prefix", args["type_prefix"]]
    return await server.cli(*argv)


def _flags(args: dict[str, Any], spec: dict[str, str]) -> list[str]:
    """MCP arguments → CLI options: ``spec`` maps each argument to its option;
    booleans become flags, lists repeat the option."""
    argv: list[str] = []
    for key, option in spec.items():
        value = args.get(key)
        if value is None or value is False:
            continue
        if value is True:
            argv.append(option)
        elif isinstance(value, list):
            for item in value:
                argv += [option, str(item)]
        else:
            argv += [option, str(value)]
    return argv


def _part(command: str, positional: list[str], spec: dict[str, str]):  # type: ignore[no-untyped-def]
    async def run(server: DevServer, args: dict[str, Any]) -> dict[str, Any]:
        argv = ["scaffold", command, *(args[p] for p in positional), *_scaffold_tail(args)]
        return await server.cli(*argv, *_flags(args, spec))

    return run


def _part_tool(
    name: str, title: str, description: str, positional: dict[str, Any], options: dict[str, Any],
    spec: dict[str, str], required: list[str] | None = None,
) -> DevTool:  # fmt: skip
    command = name.removeprefix("scaffold_")
    return DevTool(
        name,
        title,
        description + " Edits the resource class to declare it; never overwrites a name "
        "that exists. Follow the next_steps it returns.",
        _schema(
            {**positional, "package": _PACKAGE, **options, "root": _ROOT, "dry_run": _DRY},
            [*positional, "package", *(required or [])],
        ),
        _part(command, list(positional), spec),
        read_only=False,
    )


_RESOURCE = {"type": "string", "description": "Resource name (orders)"}

TOOLS = [
    DevTool(
        "project_catalogue",
        "Project catalogue",
        "This project's resources with their operations, transitions, actions, events, "
        "references, documents and permissions, as Markdown. Read it before changing a "
        "resource.",
        _schema({}),
        _catalogue,
    ),
    DevTool(
        "inspect",
        "Inspect resources",
        "The resolved definitions of the registered resources (schemas, hooks in "
        "lifecycle order, state machine, actions, permissions), as JSON.",
        _schema({"resource": {"type": "string", "description": "Only this resource"}}),
        _inspect,
    ),
    DevTool(
        "check",
        "Check architecture",
        "resource-engine check: domain imports (RE-C001), effects inside the transaction "
        "(RE-C002), after_commit misuse (RE-C003), operations only admin can perform "
        "(RE-C004), declarations the engine refuses (RE-C005), undeclared events (RE-C006). "
        "Run it after every change; output.passed tells whether it is clean.",
        _schema({"paths": {**_STRINGS, "description": "Directories to lint (default: .)"}}),
        _check,
    ),
    DevTool(
        "docs_validate",
        "Validate documentation",
        "resource-engine docs validate: missing docstrings and functional sections "
        "(RE-D001..RE-D011). Strict by default, like CI.",
        _schema(
            {
                "strict": {"type": "boolean", "default": True},
                "verbose": {"type": "boolean", "default": False, "description": "Include passes"},
            }
        ),
        _docs_validate,
    ),
    DevTool(
        "asyncapi",
        "AsyncAPI document",
        "The AsyncAPI 3.0 document of the events the app publishes and consumes. With "
        "check_against (the published file), the changes that would break consumers "
        "(exit_code 1 when there are some).",
        _schema({"check_against": {"type": "string", "description": "Published asyncapi.json"}}),
        _asyncapi,
    ),
    DevTool(
        "explain_error",
        "Explain an error code",
        "What an RE-E (runtime), RE-C (check) or RE-D (docs) code means and what to change.",
        _schema({"code": {"type": "string", "pattern": "^RE-[ECD]\\d{3}$"}}, ["code"]),
        _explain,
    ),
    DevTool(
        "scaffold_resource",
        "Scaffold a module",
        "A new module with one resource (domain, schemas, hooks, resource, persistence, "
        "tests), laid out like the reference module. Never overwrites files. Follow the "
        "next_steps it returns.",
        _schema(
            {
                "name": {"type": "string", "description": "Plural snake_case (orders)"},
                "package": _PACKAGE,
                "fields": {
                    **_STRINGS,
                    "description": "name:type[?] with type in str text int bool Decimal date "
                    "datetime; ? for optional",
                },
                "entity": {"type": "string"},
                "state_machine": {"type": "boolean"},
                "states": {"type": "string", "description": "draft,placed,shipped"},
                "transitions": {"type": "string", "description": "place:draft>placed,..."},
                "versioned": {"type": "boolean"},
                "root": _ROOT,
                "dry_run": _DRY,
            },
            ["name", "package"],
        ),
        _scaffold_resource,
        read_only=False,
    ),
    DevTool(
        "scaffold_action",
        "Scaffold an action",
        "An Action class (and its input schema) in an existing module.",
        _schema(
            {
                "resource": {"type": "string"},
                "action_class": {"type": "string", "description": "PascalCase (ApplyDiscount)"},
                "package": _PACKAGE,
                "inputs": {**_STRINGS, "description": "Input fields name:type[?]"},
                "collection": {"type": "boolean", "description": "Not tied to one record"},
                "root": _ROOT,
                "dry_run": _DRY,
            },
            ["resource", "action_class", "package"],
        ),
        _scaffold_action,
        read_only=False,
    ),
    DevTool(
        "scaffold_state_machine",
        "Scaffold a state machine",
        "A python-statemachine class in an existing module.",
        _schema(
            {
                "entity": {"type": "string", "description": "Entity class (Order)"},
                "package": _PACKAGE,
                "states": {"type": "string"},
                "transitions": {"type": "string"},
                "root": _ROOT,
                "dry_run": _DRY,
            },
            ["entity", "package"],
        ),
        _scaffold_state_machine,
        read_only=False,
    ),
    DevTool(
        "scaffold_consumer",
        "Scaffold a consumer",
        "From another service's AsyncAPI document: payload models, an EventRouter with "
        "typed handler stubs, a contract snapshot and contract tests.",
        _schema(
            {
                "name": {"type": "string", "description": "snake_case (payments)"},
                "package": _PACKAGE,
                "asyncapi": {"type": "string", "description": "Path to the provider's document"},
                "channels": _STRINGS,
                "messages": _STRINGS,
                "type_prefix": {"type": "string"},
                "root": _ROOT,
                "dry_run": _DRY,
            },
            ["name", "package", "asyncapi"],
        ),
        _scaffold_consumer,
        read_only=False,
    ),
    _part_tool(
        "scaffold_hook",
        "Scaffold a hook",
        "A hook function in application/hooks.py, registered at the given points.",
        {"resource": _RESOURCE, "function": {"type": "string", "description": "snake_case"}},
        {
            "on": {
                **_STRINGS,
                "description": "Hook points: before_create, after_update, "
                "before_transition:place, after_commit...",
            },
            "effect": {"type": "boolean", "description": "External effect (after_commit only)"},
        },
        {"on": "--on", "effect": "--effect"},
        ["on"],
    ),
    _part_tool(
        "scaffold_event",
        "Scaffold a domain event",
        "A DomainEvent in domain/events.py, declared in the resource's events.",
        {
            "resource": _RESOURCE,
            "event_class": {"type": "string", "description": "PascalCase, past tense"},
        },
        {"fields": {**_STRINGS, "description": "Payload fields name:type[?]"}},
        {"fields": "--field"},
    ),
    _part_tool(
        "scaffold_slot",
        "Scaffold a document slot",
        "A DocumentSlot in the resource's documents.",
        {"resource": _RESOURCE, "slot": {"type": "string", "description": "snake_case"}},
        {
            "content_types": _STRINGS,
            "max_mb": {"type": "integer", "minimum": 1},
            "multiple": {"type": "boolean"},
            "upload": {"type": "string", "enum": ["api", "presigned", "any"]},
            "required_for": {**_STRINGS, "description": "Transitions that need a document"},
            "processing": {"type": "boolean"},
        },
        {
            "content_types": "--content-type",
            "max_mb": "--max-mb",
            "multiple": "--multiple",
            "upload": "--upload",
            "required_for": "--required-for",
            "processing": "--processing",
        },
    ),
    _part_tool(
        "scaffold_reference",
        "Scaffold a reference",
        "A Reference to another resource; adds the id field to the entity, schemas and "
        "table when they lack it.",
        {"resource": _RESOURCE, "field": {"type": "string", "description": "customer_id"}},
        {
            "to": {"type": "string", "description": "The referenced resource"},
            "relation": {"type": "string"},
            "on_delete": {"type": "string", "enum": ["restrict", "set_null", "cascade"]},
            "required": {"type": "boolean"},
            "related_name": {"type": "string"},
        },
        {
            "to": "--to",
            "relation": "--relation",
            "on_delete": "--on-delete",
            "required": "--required",
            "related_name": "--related-name",
        },
        ["to"],
    ),
    _part_tool(
        "scaffold_policy",
        "Scaffold a seed policy",
        "A policy granting a role the resource's operations (or only reads), appended to "
        "<package>/authz.py.",
        {"resource": _RESOURCE},
        {
            "role": {"type": "string"},
            "read_only": {"type": "boolean"},
            "name": {"type": "string"},
            "variable": {"type": "string", "description": "List to append to (SEED_POLICIES)"},
        },
        {"role": "--role", "read_only": "--read-only", "name": "--name", "variable": "--variable"},
        ["role"],
    ),
]

INSTRUCTIONS = """\
Development tools for a Resource Engine project. Read the project catalogue
(project_catalogue, or the resource-engine://project resource) and the skill
guides (resource-engine://skill/...) before changing resources. After each
change run check and docs_validate and fix what they report (explain_error
tells what a code means). Use the scaffold tools for new modules, actions,
state machines and consumers instead of writing the boilerplate by hand."""


# ------------------------------------------------------------------ errors

_ROW = re.compile(r"^\|\s*(?:<a id=\"[^\"]+\"></a>)?(RE-[ECD][\d…]+(?:…[ECD]?\d+)?)\s*\|(.*)$")


def explain_error(code: str) -> dict[str, Any]:
    """The catalogue entry for ``code`` (skill references) and, for runtime
    codes, the engine's error class and its description."""
    code = code.strip().upper()
    found: dict[str, Any] = {"code": code}
    rows = []
    for name in ("errors.md", "docstrings.md"):
        text = (SKILL_DIR / "references" / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            match = _ROW.match(line)
            if match and _covers(match.group(1), code):
                cells = [c.strip() for c in match.group(2).strip().strip("|").split("|")]
                rows.append(" | ".join(c for c in cells if c))
    if rows:
        found["catalogue"] = rows
    if code.startswith("RE-E"):
        from resource_engine import errors

        for value in vars(errors).values():
            if isinstance(value, type) and getattr(value, "code", None) == code:
                found["error_class"] = f"resource_engine.errors.{value.__name__}"
                if value.__doc__:
                    found["description"] = " ".join(value.__doc__.split())
                break
    found["doc"] = f"docs/errors.md#{code.lower()}"
    if len(found) == 2:
        raise ValueError(f"No error {code} in the catalogue")
    return found


def _covers(label: str, code: str) -> bool:
    """``RE-D001…D011`` covers ``RE-D005``."""
    if "…" not in label:
        return label == code
    start, _, end = label.partition("…")
    prefix = start[:4]
    if not code.startswith(prefix):
        return False
    try:
        low = int(start[4:])
        high = int(re.sub(r"\D", "", end))
        return low <= int(code[4:]) <= high
    except ValueError:
        return False


__all__ = ["TOOLS", "DevServer", "DevTool", "explain_error"]
