from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from resource_engine.application.resource import Resource, ResourceDefinition
from resource_engine.errors import ConfigurationError, ResourceNotRegistered


class ResourceRegistry:
    """The set of resources an application exposes.

    ``register`` validates the definition, so configuration errors surface at
    bootstrap rather than on the first request. Usable as a decorator.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, ResourceDefinition] = {}
        self._engine_actions: dict[str, Any] = {}

    def register_actions(self, *actions: Any) -> None:
        """Permission actions not tied to a resource (``ActionInfo``), such as
        the explorer's ``jobs:run``: they appear in the authorization catalogue
        and the policy editor like resource actions."""
        for action in actions:
            self._engine_actions[action.name] = action

    def engine_actions(self) -> list[Any]:
        return list(self._engine_actions.values())

    def event_types(self) -> list[type[Any]]:
        """The domain events the registered resources declare (``Resource.events``)."""
        seen: dict[str, type[Any]] = {}
        for definition in self._definitions.values():
            for event in definition.resource_cls.events:
                seen.setdefault(event.__name__, event)
        return list(seen.values())

    def register[R: type[Resource]](self, resource_cls: R) -> R:
        definition = resource_cls.definition()
        if definition.parent is not None:
            base = definition.parent
            if self._definitions.get(base.name) is not base:
                self.register(base.resource_cls)  # the base comes with its subtypes
            taken = self.subtypes(base.name).get(definition.identity or "")
            if taken is not None and taken.resource_cls is not resource_cls:
                raise ConfigurationError(
                    f"{definition.name} and {taken.name} both have identity "
                    f"{definition.identity!r} in {base.name}",
                    hint="Set a distinct `identity` on one of them.",
                    doc="docs/polymorphism.md",
                )
        existing = self._definitions.get(definition.name)
        if existing is not None and existing.resource_cls is not resource_cls:
            raise ConfigurationError(
                f"Resource name {definition.name!r} is already registered by "
                f"{existing.resource_cls.__qualname__}",
                hint="Set a distinct `name` on one of the resources.",
            )
        self._definitions[definition.name] = definition
        return resource_cls

    def register_documents(self, resource_cls: type[Resource] | None = None) -> type[Resource]:
        """Register the built-in ``DocumentResource`` (or a subclass of it)."""
        from resource_engine.documents.resource import DocumentResource

        return self.register(resource_cls or DocumentResource)

    def register_authz(self) -> None:
        """Register the permission management resources (policies, custom roles,
        role assignments)."""
        from resource_engine.authz.resources import (
            PolicyResource,
            RoleAssignmentResource,
            RoleResource,
        )

        for resource_cls in (PolicyResource, RoleResource, RoleAssignmentResource):
            self.register(resource_cls)

    def register_api_keys(self) -> None:
        """Register ``authz_api_keys``: keys for machine clients (docs/api-keys.md).
        Needs ``register_authz`` (keys carry its roles)."""
        from resource_engine.authz.apikeys import ApiKeyResource

        self.register(ApiKeyResource)

    def register_audit(
        self, resource_cls: type[Resource] | None = None, *, seals: bool = False
    ) -> type[Resource]:
        """Register the built-in, read-only ``audit_log`` resource (or a subclass),
        and with ``seals=True`` the ``audit_seals`` resource used by ``AuditSealer``."""
        from resource_engine.audit.resource import AuditLogResource, AuditSealResource

        registered = self.register(resource_cls or AuditLogResource)
        if seals:
            self.register(AuditSealResource)
        return registered

    def get(self, resource: str | type[Resource]) -> ResourceDefinition:
        name = resource if isinstance(resource, str) else resource.definition().name
        try:
            return self._definitions[name]
        except KeyError:
            raise ResourceNotRegistered(
                f"Resource {name!r} is not registered",
                hint=f"Registered resources: {', '.join(self._definitions) or '(none)'}",
            ) from None

    def subtypes(self, base: str) -> dict[str, ResourceDefinition]:
        """A base resource's registered subtypes, by identity."""
        return {
            d.identity: d
            for d in self._definitions.values()
            if d.parent is not None and d.parent.name == base and d.identity is not None
        }

    def subtype_of(self, base: ResourceDefinition, entity: Any) -> ResourceDefinition | None:
        """The subtype of a base record, from its discriminator."""
        if base.discriminator is None:
            return None
        return self.subtypes(base.name).get(getattr(entity, base.discriminator, None))

    def __contains__(self, name: object) -> bool:
        return name in self._definitions

    def __iter__(self) -> Iterator[ResourceDefinition]:
        return iter(self._definitions.values())

    def __len__(self) -> int:
        return len(self._definitions)
