"""References between resources: declaration, validation and serialization.

```python
class InvoiceResource(Resource):
    references = {"customer_id": Reference("customers", name="customer")}
```

- **Integrity:** create and update check that each referenced record exists
  and that the caller can see it (RE-E108).
- **Deletes:** deleting a referenced record is refused while others point to
  it (``on_delete="restrict"``, RE-E109). ``set_null`` and ``cascade`` update
  or delete the dependents through the manager, in the same transaction and
  as the same caller.
- **Reads:** ``get``/``list`` take ``expand=("customer",)`` and attach the
  related records (``None`` when the caller may not read them). ``list``
  accepts ``filters={"customer.country": "ES"}`` on the related resource's
  filterable fields.

See docs/relationships.md.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from resource_engine.errors import ConfigurationError

if TYPE_CHECKING:
    from resource_engine.application.registry import ResourceRegistry
    from resource_engine.application.resource import ResourceDefinition

type OnDelete = Literal["restrict", "set_null", "cascade"]

ON_DELETE = ("restrict", "set_null", "cascade")
DOC = "docs/relationships.md"
_EXPANDED = "_re_expanded"


@dataclass(frozen=True)
class Reference:
    """A field holding the id of a record of another resource.

    Args:
        resource: The referenced resource's name.
        name: Name of the relation, used by ``expand`` and related filters
            (default: the field without ``_id``: ``customer_id`` → ``customer``).
        on_delete: What deleting the referenced record does to records that
            point to it: ``restrict`` (refused, RE-E109), ``set_null`` (the
            field is set to ``None`` through ``update``) or ``cascade`` (they
            are deleted through ``delete``).
        description: What the relation means, for the generated docs.
        related_name: Name of the reverse relation on the referenced resource
            (default: this resource's name: ``customers`` gets ``invoices``):
            ``GET /customers/{id}/invoices``, ``expand=["invoices"]``.
        reverse: ``False`` exposes no reverse relation.
    """

    resource: str
    name: str | None = None
    on_delete: OnDelete = "restrict"
    description: str | None = None
    related_name: str | None = None
    reverse: bool = True


@dataclass(frozen=True)
class ReferenceDefinition:
    """A validated reference: ``field`` of ``owner`` points to ``target``."""

    owner: str
    field: str
    target: str
    name: str
    on_delete: OnDelete
    description: str | None
    #: The reverse relation's name on ``target``; ``None`` when not exposed.
    related_name: str | None = None


def normalize_references(
    owner: str, qualname: str, declared: Mapping[str, Reference], known_fields: set[str]
) -> dict[str, ReferenceDefinition]:
    """Validate ``Resource.references`` on its own (targets are checked by
    ``check_references`` once every resource is registered). By relation name."""
    refs: dict[str, ReferenceDefinition] = {}
    for field_name, ref in declared.items():
        if not isinstance(ref, Reference):
            raise ConfigurationError(
                f"{qualname}: references[{field_name!r}] must be a Reference, not {ref!r}",
                hint='references = {"customer_id": Reference("customers")}',
                doc=DOC,
            )
        if field_name not in known_fields:
            raise ConfigurationError(
                f"{qualname}: reference field {field_name!r} is not part of the read or "
                "write schemas",
                doc=DOC,
            )
        if ref.on_delete not in ON_DELETE:
            raise ConfigurationError(
                f"{qualname}: on_delete of {field_name!r} must be one of {ON_DELETE}",
                doc=DOC,
            )
        name = ref.name or field_name.removesuffix("_id")
        if name == field_name or name in known_fields:
            raise ConfigurationError(
                f"{qualname}: the relation name {name!r} of {field_name!r} clashes with a field",
                hint="Pass name=... to the Reference.",
                doc=DOC,
            )
        if name in refs:
            raise ConfigurationError(f"{qualname}: two references are named {name!r}", doc=DOC)
        refs[name] = ReferenceDefinition(
            owner=owner,
            field=field_name,
            target=ref.resource,
            name=name,
            on_delete=ref.on_delete,
            description=ref.description,
            related_name=(ref.related_name or owner) if ref.reverse else None,
        )
    return refs


def _nullable(annotation: Any) -> bool:
    if annotation is None or annotation is type(None) or annotation is Any:
        return True
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return any(_nullable(a) for a in typing.get_args(annotation))
    if origin is typing.Annotated:
        return _nullable(typing.get_args(annotation)[0])
    return False


def check_references(registry: ResourceRegistry) -> dict[str, list[ReferenceDefinition]]:
    """Validate every reference against the registry; returns the references
    pointing to each resource (its dependents), by target name."""
    names = {d.name for d in registry}
    dependents: dict[str, list[ReferenceDefinition]] = {}
    for d in registry:
        for ref in d.references.values():
            where = f"{d.name}.{ref.field}"
            if ref.target not in names:
                raise ConfigurationError(
                    f"{where} references {ref.target!r}, which is not registered",
                    hint="Register the referenced resource in the same registry.",
                    doc=DOC,
                )
            if ref.on_delete == "set_null":
                update = d.schemas.get("update")
                field = update.model_fields.get(ref.field) if update is not None else None
                if not d.enabled("update") or field is None or not _nullable(field.annotation):
                    raise ConfigurationError(
                        f"{where}: on_delete='set_null' needs update enabled with "
                        f"{ref.field!r} in the update schema, accepting None",
                        hint="The field is cleared through manager.update as the caller.",
                        doc=DOC,
                    )
            if ref.on_delete == "cascade" and not d.enabled("delete"):
                raise ConfigurationError(
                    f"{where}: on_delete='cascade' needs delete enabled on {d.name!r}",
                    doc=DOC,
                )
            dependents.setdefault(ref.target, []).append(ref)
    _check_cycles(dependents)
    return dependents


@dataclass(frozen=True)
class LinkDefinition:
    """A many-to-many relation through a join resource: from a ``source``
    record, the ``target`` records linked to it by ``join`` rows
    (``join.source_field`` → source, ``join.target_field`` → target)."""

    join: str
    source: str
    source_field: str
    target: str
    target_field: str
    name: str
    description: str | None = None

    @property
    def owner(self) -> str:
        """The resource whose records the relation lists."""
        return self.target

    @property
    def field(self) -> None:
        return None


def normalize_link(
    qualname: str,
    link: Sequence[str] | None,
    link_names: Mapping[str, str],
    references: Mapping[str, ReferenceDefinition],
) -> tuple[str, str] | None:
    """Validate ``Resource.link``: two of the resource's reference fields."""
    if link is None:
        if link_names:
            raise ConfigurationError(f"{qualname}: link_names needs link", doc=DOC)
        return None
    fields = {ref.field for ref in references.values()}
    pair = tuple(link)
    if len(pair) != 2 or pair[0] == pair[1] or not set(pair) <= fields:
        raise ConfigurationError(
            f"{qualname}: link must name two different reference fields, not {link!r}",
            hint='link = ("invoice_id", "tag_id"), both declared in references.',
            doc=DOC + "#many-to-many",
        )
    unknown = set(link_names) - set(pair)
    if unknown:
        raise ConfigurationError(
            f"{qualname}: link_names keys must be the link fields, not {sorted(unknown)}",
            doc=DOC + "#many-to-many",
        )
    return pair  # type: ignore[return-value]


#: Path segments of a record's own routes; reverse relations cannot use them.
RESERVED_RELATIONS = frozenset({"transitions", "history", "actions", "documents"})


def _links(registry: ResourceRegistry) -> dict[str, list[LinkDefinition]]:
    """Many-to-many relations by the resource that gets them."""
    out: dict[str, list[LinkDefinition]] = {}
    for d in registry:
        if d.link is None:
            continue
        by_field = {r.field: r for r in d.references.values()}
        a, b = (by_field[f] for f in d.link)
        for source, target in ((a, b), (b, a)):
            if not (d.enabled("list") and registry.get(target.target).enabled("list")):
                continue
            out.setdefault(source.target, []).append(
                LinkDefinition(
                    join=d.name,
                    source=source.target,
                    source_field=source.field,
                    target=target.target,
                    target_field=target.field,
                    name=d.link_names.get(source.field, target.target),
                    description=d.description and d.description.splitlines()[0],
                )
            )
    return out


def reverse_relations(
    registry: ResourceRegistry, dependents: Mapping[str, list[ReferenceDefinition]]
) -> dict[str, dict[str, ReferenceDefinition | LinkDefinition]]:
    """The reverse relations (``customers`` → ``invoices``) and many-to-many
    links (``invoices`` → ``tags``) of each resource, by resource and name."""
    out: dict[str, dict[str, ReferenceDefinition | LinkDefinition]] = {}
    links = _links(registry)
    for target in sorted(set(dependents) | set(links)):
        refs: list[Any] = list(dependents.get(target, ())) + list(links.get(target, ()))
        definition = registry.get(target)
        read = definition.schemas.get("read")
        fields = set(read.model_fields) if read is not None else set()
        named: dict[str, ReferenceDefinition | LinkDefinition] = {}
        for ref in refs:
            if isinstance(ref, LinkDefinition):
                name = ref.name
                where = f"{ref.join} link"
            else:
                name = ref.related_name
                if name is None or not registry.get(ref.owner).enabled("list"):
                    continue  # no reverse relation without list on the referencing resource
                where = f"{ref.owner}.{ref.field}"
            clash = (
                "another reverse relation"
                if name in named
                else "a relation"
                if name in definition.references
                else "a field"
                if name in fields
                else "a route"
                if name in RESERVED_RELATIONS
                else None
            )
            if clash or not name.isidentifier():
                raise ConfigurationError(
                    f"{where}: the relation {name!r} on {target} "
                    + (f"clashes with {clash}" if clash else "is not a valid name"),
                    hint="Pass related_name=... (or reverse=False) to the Reference, "
                    "or link_names=... on the join resource.",
                    doc=DOC + "#reverse-relations",
                )
            named[name] = ref
        if named:
            out[target] = named
    return out


def parse_expand(item: str) -> tuple[str, int | None]:
    """``"invoices:25"`` → ``("invoices", 25)``; ``"customer"`` → ``("customer", None)``."""
    name, sep, size = item.partition(":")
    if not sep:
        return name.strip(), None
    try:
        return name.strip(), int(size)
    except ValueError:
        return name.strip(), -1


@dataclass(frozen=True)
class RelatedPage:
    """An embedded page of a reverse relation (``expand=["invoices"]``)."""

    items: list[Any]
    total: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.total > len(self.items)


def _check_cycles(dependents: Mapping[str, list[ReferenceDefinition]]) -> None:
    edges = {
        target: {r.owner for r in refs if r.on_delete == "cascade"}
        for target, refs in dependents.items()
    }
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 1:
            cycle = " → ".join([*path[path.index(node) :], node])
            raise ConfigurationError(
                f"Cascading deletes form a cycle: {cycle}",
                hint="Use on_delete='restrict' or 'set_null' on one of the references.",
                doc=DOC,
            )
        if state.get(node) == 2:
            return
        state[node] = 1
        for child in sorted(edges.get(node, ())):
            visit(child, [*path, node])
        state[node] = 2

    for node in sorted(edges):
        visit(node, [])


# ----------------------------------------------------------------- results


def attach(model: Any, expanded: Mapping[str, Any]) -> Any:
    """Attach expanded related records to a read model (in place)."""
    if not isinstance(model, BaseModel) or not expanded:
        return model
    private = dict(model.__pydantic_private__ or {})
    private[_EXPANDED] = {**private.get(_EXPANDED, {}), **expanded}
    object.__setattr__(model, "__pydantic_private__", private)
    return model


def expansions(model: Any) -> dict[str, Any]:
    """The related records attached by ``expand``, by relation name."""
    private = getattr(model, "__pydantic_private__", None)
    if not isinstance(private, dict):
        return {}
    return dict(private.get(_EXPANDED, {}))


def dump_read(value: Any) -> Any:
    """JSON-compatible form of a read result for entry adapters: hidden fields
    left out, expanded related records nested under their relation name."""
    from resource_engine.authz.fields import hidden_fields

    if isinstance(value, RelatedPage):
        return {
            "items": [dump_read(i) for i in value.items],
            "total": value.total,
            "limit": value.limit,
            "has_more": value.has_more,
        }
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json", by_alias=True, exclude=set(hidden_fields(value)))
        for name, related in expansions(value).items():
            data[name] = dump_read(related)
        return data
    if isinstance(value, list | tuple):
        return [dump_read(v) for v in value]
    if isinstance(value, Mapping):
        return {k: dump_read(v) for k, v in value.items()}
    return value


def needs_dump(value: Any) -> bool:
    """Whether ``value`` must be serialized with ``dump_read`` (hidden fields
    or expansions), not by validating it against its response model."""
    from resource_engine.authz.fields import hidden_fields

    return bool(hidden_fields(value)) or bool(expansions(value))


def split_filters(filters: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Own filters, and related filters by relation name (``customer.country``)."""
    own: dict[str, Any] = {}
    related: dict[str, dict[str, Any]] = {}
    for key, value in filters.items():
        if "." in key:
            name, _, field = key.partition(".")
            related.setdefault(name, {})[field] = value
        else:
            own[key] = value
    return own, related


def related_filter_names(definition: ResourceDefinition, registry: ResourceRegistry) -> list[str]:
    """Every ``<relation>.<field>`` filter ``list`` accepts."""
    names = []
    for ref in definition.references.values():
        target = registry.get(ref.target)
        names += [f"{ref.name}.{f}" for f in target.filterable]
    return names


def unique(values: Iterable[Any]) -> list[Any]:
    seen: list[Any] = []
    for v in values:
        if v is not None and v not in seen:
            seen.append(v)
    return seen


__all__ = [
    "LinkDefinition",
    "Reference",
    "ReferenceDefinition",
    "RelatedPage",
    "attach",
    "check_references",
    "dump_read",
    "expansions",
    "needs_dump",
    "normalize_references",
    "parse_expand",
    "related_filter_names",
    "reverse_relations",
    "split_filters",
]
