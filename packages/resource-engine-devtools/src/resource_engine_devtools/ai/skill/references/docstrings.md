# Docstrings

Functional documentation is generated from docstrings and checked in CI with
`resource-engine docs validate --strict`. Use Google style with these functional
sections (title, colon, indented body):

| Section | Where |
|---|---|
| `Business area:` | Resources |
| `Actors:` | Resources, actions |
| `Preconditions:` | Actions, hooks |
| `Postconditions:` | Actions (required, RE-D007) |
| `Business Rules:` | Entities, resources |
| `Effects:` | Actions, hooks with effects |
| `Exceptions:` | Anything that raises domain errors |
| `Examples:` | Anything |
| `States:` / `Transitions:` | State machines: one `name: description` per state and per transition |

The first line is a one-sentence summary. Write in the project's language
(the reference module uses Catalan).

## What `docs validate` requires

| Code | Level | Rule |
|---|---|---|
| RE-D001 | error | Every resource has a docstring |
| RE-D002 | error | Every enabled CRUD operation has an `operation_docs` entry |
| RE-D003 | error | Every state machine has a docstring |
| RE-D004 | warning | Every state is described under `States:` |
| RE-D005 | warning | Every transition is described under `Transitions:` |
| RE-D006 | error | Every action has a docstring |
| RE-D007 | warning | Every action has `Postconditions:` |
| RE-D008 | warning | Every hook has a docstring |
| RE-D009 | warning | Every document slot has a `description` |
| RE-D010 | warning | No description is still a `TODO` placeholder (from `scaffold`) |
| RE-D011 | warning | Every declared domain event has a docstring |

`--strict` makes warnings fail too; use it in CI.

## Examples

```python
class InvoiceResource(Resource):
    """Gestió de factures de l'organització.

    Permet crear, consultar i modificar factures, així com gestionar-ne el
    procés d'aprovació i cobrament.

    Business area:
        Facturació

    Actors:
        - Comptable
        - Responsable
    """

    operation_docs = {
        "list": "Llista les factures, filtrables per estat i client.",
        "get": "Consulta una factura.",
    }
```

```python
class RecalculateTotals(Action):
    """Aplica un descompte percentual a l'import d'una factura.

    Preconditions:
        La factura és un esborrany.

    Postconditions:
        L'import queda reduït pel percentatge indicat, arrodonit a cèntims.

    Effects:
        Emet l'esdeveniment ``InvoiceTotalsRecalculated``.
    """
```

```python
@dataclass
class Invoice:
    """Factura emesa a un client.

    Business Rules:
        - L'import total no pot ser negatiu.
        - Una factura només es pot modificar mentre és un esborrany.
    """
```

Docstrings describe behaviour; they never replace it. Permissions are enforced
by policies, transitions by the state machine, rules by the domain code.

## Where the docs go

`resource-engine docs generate` writes them as Markdown and `resource-engine
docs site --out site` as a static website for non-developers (diagrams,
permission matrix, events, search). Both come from these docstrings: fix the
text here, never in the generated files.
