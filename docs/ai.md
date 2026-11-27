# AI assistance

The framework explains itself to AI coding agents with three layers:

| Layer | What it explains | Delivered as | Changes |
|---|---|---|---|
| Framework | How to write resources, hooks, state machines, actions and permissions; the lifecycle; anti-patterns | The skill and `llms.txt`, shipped in `resource-engine-devtools` | With each package version |
| Project | This application's resources, operations, transitions, actions and permissions | `project.md`, generated from the registry | With each code change |
| Verification | Whether the code is right | `check`, `docs validate`, `inspect` with `--json` | On every run |

## Installing

```bash
resource-engine ai install                                   # claude, agents
resource-engine ai install --target claude,agents,cursor,copilot
resource-engine ai install --mcp                             # also register the MCP server
```

| Target | Writes |
|---|---|
| `claude` | The skill in `.claude/skills/resource-engine/` |
| `agents` | A managed block in `AGENTS.md` |
| `cursor` | `.cursor/rules/resource-engine.mdc` |
| `copilot` | A managed block in `.github/copilot-instructions.md` |

- **Where the skill goes:** the skill files go to one place. That's the Claude
  skill folder, or `.resource-engine/skill/` without the `claude` target. The
  other targets point to it.
- **Managed blocks:** they sit between `<!-- resource-engine:begin -->` and
  `<!-- resource-engine:end -->`. The rest of `AGENTS.md` and
  `copilot-instructions.md` stays as your team wrote it.
- **Project catalogue:** `project.md` is generated from the registry
  (`--app`, or `registry` in `[tool.resource-engine]`).
- **Lock file:** `.resource-engine/ai-lock.json` records the version, the
  targets and a hash of every file and block written.

- **MCP server (`--mcp`):** the [development MCP server](#mcp-server) is
  registered for the targets' agents:
  - `.mcp.json` for `claude`;
  - `.cursor/mcp.json` for `cursor`;
  - `.vscode/mcp.json` for `copilot`.

  Only the `resource-engine` entry is managed, so other servers and keys in
  those files stay. Files with comments are refused; add the entry by hand
  there.
- **The MCP command:** by default it is `uv run resource-engine mcp` when the
  project has a `uv.lock`, and `resource-engine mcp` otherwise.
  `--mcp-command "poetry run resource-engine mcp"` chooses another.
- **Keeping it:** the choice is kept in the lock file. `ai sync` keeps the
  entries current, and `ai sync --check` reports a missing or edited entry.
  Installing again without `--mcp` removes the entries nobody edited.

Commit all of it, so every agent on the team sees the same skill and tools.

## What the skill contains

```text
SKILL.md                 workflow, rules that must not be broken, verification commands
llms.txt                 index for tools that read llms.txt
project.md               this project's catalogue (generated)
references/
  resource.md            Resource attributes, schemas, actions, events, documents, wiring
  lifecycle.md           step order, hook points, OperationContext, external effects
  state-machines.md      states, transitions, callbacks, guards
  permissions.md         action names, policies, conditions, fields, tenants
  docstrings.md          functional sections and docs validate rules
  testing.md             in-memory adapters and what to test
  errors.md              every RE-* code and what to change
  anti-patterns.md       common mistakes, wrong and right code
examples/invoices/       the reference module
```

**The reference module** is a copy of `examples/invoices`, which runs in the
framework's test suite. A test fails if the copy drifts, so the example the
agents read is always one that works.

## Keeping it current

```bash
resource-engine ai sync            # rewrite what changed, remove files dropped by the new version
resource-engine ai sync --check    # CI: exit 1 if anything is out of date
```

`--check` compares what is installed with what an install would write now. It
reports four states:

| State | Meaning |
|---|---|
| `missing` | A file or block isn't there |
| `out_of_date` | A new package version, or the project changed (`project.md`) |
| `modified` | Someone edited a managed file; `sync` will overwrite it |
| `stale` | A file of an older skill version that `sync` will remove |

When `sync` removes files from older versions, it keeps any file that was
edited after it was installed.

## MCP server

`resource-engine mcp` serves the same commands as
[MCP](https://modelcontextprotocol.io) tools over stdio. Coding agents can then
call them directly instead of building shell commands. It needs the `mcp`
extra:

```bash
uv add --dev 'resource-engine-devtools[mcp]'     # or: pip install 'resource-engine-devtools[mcp]'
resource-engine ai install --mcp                 # .mcp.json, and Cursor / VS Code with their targets
```

For other clients, such as Claude Desktop or a user-wide configuration, use the
same command:

```json
{"mcpServers": {"resource-engine": {"command": "uv", "args": ["run", "resource-engine", "mcp"]}}}
```

| Tool | Does |
|---|---|
| `project_catalogue` | This project's resources, operations, transitions, actions, events, references and permissions (`ai context`) |
| `inspect` | Resolved definitions as JSON, all or one resource |
| `check` | Architecture, hook/effect, gate and event lints (RE-C001…C006) |
| `docs_validate` | Documentation rules (RE-D001…D011), strict by default; only the findings |
| `asyncapi` | The AsyncAPI document, or with `check_against` the changes that would break consumers |
| `explain_error` | What an `RE-E`, `RE-C` or `RE-D` code means and what to change |
| `scaffold_resource`, `scaffold_action`, `scaffold_state_machine`, `scaffold_consumer`, `scaffold_hook`, `scaffold_event`, `scaffold_slot`, `scaffold_reference`, `scaffold_policy` | The [scaffold](scaffold.md) commands, with `dry_run` |

- **Fresh runs:** each project tool runs the CLI with `--json` in a new
  process in the project directory (`--project` to choose another). It sees
  the code as the agent just left it, never a stale import, and nothing the
  application prints can corrupt the protocol.
- **Results:**
  - findings such as violations or missing docstrings come back as normal
    results with `exit_code` 1, because they are the answer;
  - real failures are tool errors: a refused scaffold, a crash, or an
    application that does not import.
- **Annotations:** the scaffold tools never overwrite files, and they are
  marked as not read-only.
- **Resources:** the server also offers the skill's guides
  (`resource-engine://skill/SKILL.md`, `.../references/lifecycle.md`...) and the
  live project catalogue (`resource-engine://project`). Agents can follow the
  conventions even where the skill isn't installed.

This server is for building the application. To let agents *use* it (call its
operations with a user's permissions), see the [runtime MCP server](mcp.md).

## In CI

```bash
resource-engine check --json
resource-engine docs validate --strict
resource-engine ai sync --check
```

## Does it help?

[`evals/`](../evals/README.md) runs reference tasks with an agent, with and
without the skill, and scores each result four ways: `check`,
`docs validate --strict`, the project's tests, and hidden tests.

```bash
uv run python evals/run.py --agent 'claude -p "$(cat {prompt_file})" --permission-mode acceptEdits'
```
