from __future__ import annotations

from collections.abc import Iterator

from resource_engine.application.resource import Resource, ResourceDefinition
from resource_engine.errors import ConfigurationError, ResourceNotRegistered


class ResourceRegistry:
    """The set of resources an application exposes.

    ``register`` validates the definition, so configuration errors surface at
    bootstrap rather than on the first request. Usable as a decorator.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, ResourceDefinition] = {}

    def register[R: type[Resource]](self, resource_cls: R) -> R:
        definition = resource_cls.definition()
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

    def register_audit(self, resource_cls: type[Resource] | None = None) -> type[Resource]:
        """Register the built-in, read-only ``audit_log`` resource (or a subclass)."""
        from resource_engine.audit.resource import AuditLogResource

        return self.register(resource_cls or AuditLogResource)

    def get(self, resource: str | type[Resource]) -> ResourceDefinition:
        name = resource if isinstance(resource, str) else resource.definition().name
        try:
            return self._definitions[name]
        except KeyError:
            raise ResourceNotRegistered(
                f"Resource {name!r} is not registered",
                hint=f"Registered resources: {', '.join(self._definitions) or '(none)'}",
            ) from None

    def __contains__(self, name: object) -> bool:
        return name in self._definitions

    def __iter__(self) -> Iterator[ResourceDefinition]:
        return iter(self._definitions.values())

    def __len__(self) -> int:
        return len(self._definitions)
