# Skill evaluation

Measures whether the AI skill helps. The proposal's benchmark (section 5.8) is
tasks run by an agent with the skill and without it, scored by `check`,
`docs validate` and the tests.

```bash
uv run python evals/run.py --agent reference          # check the harness: 8/8 in both modes
uv run python evals/run.py \
  --agent 'claude -p "$(cat {prompt_file})" --permission-mode acceptEdits' \
  --out evals/results --keep evals/runs
```

## What runs

For every task and mode, the runner:

1. copies `fixture/` to a temporary directory;
2. applies the solutions of the tasks in `setup`;
3. with the skill, runs `resource-engine ai install`;
4. writes the prompt to `prompt.md` next to the project and runs the agent
   inside it;
5. scores the result:

| Check | Passes when |
|---|---|
| `check` | `resource-engine check` reports no violations |
| `docs` | `resource-engine docs validate --strict` passes |
| `tests` | The project's tests pass, the agent's included |
| `hidden` | The task's hidden tests pass. They are copied in after the agent finishes |

`--agent` takes a shell command. `{prompt_file}` and `{prompt}` (shell-quoted)
are replaced, and the workspace's `bin` directory comes first on `PATH`, so
`resource-engine` and `pytest` are found. `--agent reference` applies each
task's reference solution instead, and `--agent noop` changes nothing; both
test the harness without an agent. `--out` writes `results-<time>.json` and
`.md`, and `--keep` keeps every project and `agent.log`.

## The fixture

`fixture/` is a small help desk with one module, `customers`, made with
`resource-engine scaffold` and documented. It has an `agent` role and seed
policies, and it passes all four checks as it is.

## Tasks

| Task | Asks for | Traps |
|---|---|---|
| `tickets-with-state-machine` | A `tickets` module: fields, CRUD without delete, a four-state machine, a domain rule, two roles with seed policies, tests | State in the create schema; operations no role can perform (RE-C004); undocumented states |
| `notify-and-reassign` | On top of the first: e-mail the customer when a ticket is resolved, and a `reassign` action for agents | Sending the e-mail inside the transaction (RE-C002; a hidden test makes the commit fail and expects no e-mail) |

Each task has:

- `task.toml`, with the prompt, the `setup` tasks and the hidden tests;
- `hidden/`, the hidden tests;
- `solution/`, the reference solution, a set of files laid over the fixture.

`evals/test_evals.py` checks that the reference solutions score full marks,
that doing nothing fails, and that the in-transaction e-mail is caught.

## Adding a task

1. Write `tasks/<id>/task.toml`, with a prompt that names every identifier
   the hidden tests use.
2. Write the hidden tests against the application's public behaviour: the
   manager, the registry and the seed. Don't test file layout.
3. Write the reference solution and make it pass all four checks:
   `uv run python evals/run.py --agent reference --task <id>`.
