---
name: resource-engine
description: Build and change business backends written with Resource Engine (Python) - declarative resources, CRUD, state machines and transitions, actions, lifecycle hooks, permissions, events and documents. Use it whenever the code imports resource_engine, defines a Resource, Action, StateMachine or hook, or the task is adding or changing a resource, operation, transition, permission or event in such a project.
---

# Resource Engine

Resource Engine runs every operation of a resource through one lifecycle:
validation, permissions, hooks, persistence in one transaction, audit, events
after commit. You declare *what* exists; the engine does the plumbing. Your code
is the domain (entities, rules, state machines) and the business logic (hooks,
actions).

Before writing code, look at the project: `resource-engine inspect --json` shows
every resource as the engine resolved it (operations, schemas, hooks in their
real order, transitions, actions, permissions). `resource-engine ai context`
prints a compact catalogue; the installed copy is `project.md` next to this file.

## Workflow

1. **Find the module.** Each business module has `domain/` (entities, state
   machine, events, `DomainError`s), `application/` (resource, schemas, hooks,
   actions) and `infrastructure/` (SQLAlchemy mapping). The composition root
   registers resources (`bootstrap.py`). Follow `examples/invoices/` in this
   skill: it is the reference module and runs in the framework's tests.
2. **Write the domain first.** Plain classes or dataclasses, no framework
   imports. Business rules are methods that raise a `DomainError` subclass.
3. **Declare the resource.** See [references/resource.md](references/resource.md).
   For a new module or any part of one, start from the scaffold instead of
   writing the boilerplate. The part commands also declare what they write on
   the resource class:

   ```bash
   resource-engine scaffold resource orders --package shop.orders --field customer:str --field total:Decimal --state-machine
   resource-engine scaffold action orders ApplyDiscount --package shop.orders --input percent:Decimal
   resource-engine scaffold state-machine Order --package shop.orders --states new,paid --transitions 'pay:new>paid'
   resource-engine scaffold hook orders check_credit --package shop.orders --on before_create
   resource-engine scaffold event orders OrderShipped --package shop.orders --field carrier:str
   resource-engine scaffold slot orders delivery_note --package shop.orders --content-type application/pdf
   resource-engine scaffold reference orders customer_id --package shop.orders --to customers
   resource-engine scaffold policy orders --package shop.orders --role clerk
   ```

   Then follow the printed next steps and replace every `TODO` docstring
   (`docs validate` reports them as RE-D010).
4. **Add behaviour in the right place.**
   - A field-level rule or invariant → a domain method, called from a hook.
   - A process with states → a state machine with transitions
     ([references/state-machines.md](references/state-machines.md)).
   - A business operation that is not CRUD or a transition → an `Action`.
   - An email → record `EmailRequested(template=..., to=..., context=...)` in
     a hook or action inside the transaction; the worker sends it after the
     commit (`Mailer.router()`). Templates live in `templates/email/`
     (`<name>.subject.txt`, `.txt`, `.html`, per locale); `check` reports
     missing ones (RE-C007).
   - A text people read (an email line, a message) → write it in the
     project's source language inside `_()` / `{% trans %}`; run
     `resource-engine i18n extract` and `update`, and leave the `.po`
     translations to translators (`docs/i18n.md`). Never translate JSON
     data; errors already carry a translated `title`.
   - Something else to do after the change is saved (call another system)
     → record a domain event and handle it in a consumer, or an `after_commit`
     hook marked `@external_effect`.
   - Who may do it → permissions, not hooks: a policy in the seed or a
     system-role grant for every new operation
     ([references/permissions.md](references/permissions.md)).
5. **Document with docstrings** in the functional convention
   ([references/docstrings.md](references/docstrings.md)). Documentation is
   generated from them and checked in CI.
6. **Map the entity** in `infrastructure/` with SQLAlchemy imperative mapping
   and register the resource in the composition root.
7. **Test** against the in-memory adapters (`resource_engine.testing`), then
   over HTTP or SQLite if the change touches them
   ([references/testing.md](references/testing.md)).
8. **Verify**, and fix everything reported:

   ```bash
   resource-engine check --json          # architecture, hook/effect and gate lints
   resource-engine docs validate --strict
   pytest
   resource-engine ai sync --check       # skill and project context up to date
   ```

   With the `resource-engine` MCP server configured (`resource-engine mcp`),
   call its `check`, `docs_validate`, `explain_error` and `scaffold_*` tools
   instead of shell commands.

## Rules that must not be broken

- **No external effects before commit.** Hooks before `after_commit` and
  `Action.execute` run inside the transaction: no HTTP calls, e-mails, messages
  or payments there. Record a domain event (`ctx.record_event`) or use an
  `after_commit` hook marked `@external_effect`. (`check`: RE-C002; engine:
  RE-E104.)
- **State changes only through transitions.** Never put the state field in a
  `create` or `update` schema (RE-E004 at registration), and never assign it
  in a hook or an action: that skips the transition's permission, hooks and
  event.
- **`domain/` imports nothing from FastAPI, SQLAlchemy, Pydantic or HTTP
  clients** (`check`: RE-C001).
- **Business processes are actions or transitions, not hooks.** Hooks guard
  and enrich an operation; they do not start new ones.
- **Permissions live in policies and system roles**, never in `if user...`
  checks inside hooks.
- **`after_commit` cannot change the operation.** The transaction is over: no
  `ctx.record_event`, `ctx.repository()`, `ctx.uow` or changes to `ctx.entity`
  (`check`: RE-C003).
- **Document every functional element** (resource, operations, state machine,
  states, transitions, actions) with docstrings in the convention.

Common mistakes and their fixes: [references/anti-patterns.md](references/anti-patterns.md).

## References

| File | Read it when |
|---|---|
| [resource.md](references/resource.md) | Declaring or changing a resource, schemas, operations, actions, documents |
| [lifecycle.md](references/lifecycle.md) | Choosing a hook point; understanding order, rollback and events |
| [state-machines.md](references/state-machines.md) | Adding states or transitions |
| [permissions.md](references/permissions.md) | Who may do what, fields, tenants |
| [docstrings.md](references/docstrings.md) | Writing docstrings that `docs validate` accepts |
| [testing.md](references/testing.md) | Writing tests |
| [errors.md](references/errors.md) | An `RE-*` code shows up |
| [anti-patterns.md](references/anti-patterns.md) | Before finishing: check your code against it |

Engine errors carry a stable code, a hint and a documentation link: read the
hint, it usually says exactly what to change.
