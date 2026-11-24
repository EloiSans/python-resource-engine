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

Commit all of it, so every agent on the team sees the same skill.

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

## In CI

```bash
resource-engine check --json
resource-engine docs validate --strict
resource-engine ai sync --check
```
