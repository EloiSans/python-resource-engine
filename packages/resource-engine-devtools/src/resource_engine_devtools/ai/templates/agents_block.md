## Resource Engine

This project is built with Resource Engine {version}. Before changing
resources, operations, transitions, actions, hooks or permissions, read the
skill at `{skill_path}/SKILL.md` and the project catalogue at
`{skill_path}/project.md`.

Rules:

- No external effects (HTTP, e-mail, messages) before commit: record a domain
  event, or use an `after_commit` hook marked `@external_effect`.
- State changes only through transitions; never put the state field in a
  create or update schema.
- `domain/` imports nothing from FastAPI, SQLAlchemy, Pydantic or HTTP clients.
- Business processes are actions or transitions, not hooks; permissions are
  policies or system roles, never checks in hooks.
- Document every resource, operation, state, transition and action with
  docstrings in the functional convention.

Verify before finishing:

```bash
resource-engine check --json
resource-engine docs validate --strict
pytest
```
