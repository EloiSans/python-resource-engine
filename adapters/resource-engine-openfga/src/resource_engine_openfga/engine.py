"""``OpenFgaEngine``: relationship checks against an OpenFGA store."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from openfga_sdk.client import ClientConfiguration, OpenFgaClient
from openfga_sdk.client.models import ClientCheckRequest, ClientListObjectsRequest, ClientTuple
from resource_engine.authz.catalog import action_matches
from resource_engine.errors import ConditionNotSupported, RemoteCallFailed
from resource_engine.ports.authz import AuthzRequest

if TYPE_CHECKING:
    from resource_engine.application.registry import ResourceRegistry


def safe_id(value: Any) -> str:
    """OpenFGA ids cannot contain ``:``, ``#`` or spaces (``Principal.id`` is
    ``provider:subject``): ``:`` becomes ``|``, ``#`` ``~`` and spaces ``_``."""
    return str(value).replace(":", "|").replace("#", "~").replace(" ", "_")


class OpenFgaEngine:
    """``PolicyEngine`` over OpenFGA (relationship-based access control).

    Args:
        client: An ``OpenFgaClient`` bound to a store (and model), or pass
            ``api_url``/``store_id``/``model_id`` to build one.
        relations: Action pattern → relation, e.g. ``{"invoices:get": "viewer",
            "invoices:create": "creator", "invoices:read:notes": "auditor"}``.
            The first matching pattern wins; unmapped actions are denied.
        user_type: Type of principals (``user:<Principal.id>``).
        role_type: Type of application roles. The caller's roles are sent as
            contextual tuples ``user:<id> member role:<name>``, so models can grant
            through ``role#member`` without syncing role assignments. ``None``
            disables it.
        object_types: Resource name → OpenFGA type (default: the resource name).
        collection_id: Object standing for the whole collection, used by actions
            that have no record (``<type>:collection``).
        collection_actions: Patterns of actions checked on the collection object
            (default: ``*:create``).
        max_objects: Row filters list at most this many objects; beyond it the
            filter is refused (``ConditionNotSupported``) rather than truncated.
    """

    def __init__(
        self,
        client: OpenFgaClient | None = None,
        *,
        relations: Mapping[str, str],
        api_url: str | None = None,
        store_id: str | None = None,
        model_id: str | None = None,
        user_type: str = "user",
        role_type: str | None = "role",
        object_types: Mapping[str, str] | None = None,
        collection_id: str = "collection",
        collection_actions: Iterable[str] = ("*:create",),
        max_objects: int = 1000,
    ) -> None:
        if client is None:
            client = OpenFgaClient(
                ClientConfiguration(
                    api_url=api_url, store_id=store_id, authorization_model_id=model_id
                )
            )
        self.client = client
        self.relations = dict(relations)
        self.user_type = user_type
        self.role_type = role_type
        self.object_types = dict(object_types or {})
        self.collection_id = collection_id
        self.collection_actions = tuple(collection_actions)
        self.max_objects = max_objects
        self._id_types: dict[str, type[Any]] = {}

    def bind(self, registry: ResourceRegistry) -> None:
        self._id_types = {d.name: d.id_type for d in registry}

    # ------------------------------------------------------------------ naming

    def relation(self, action: str) -> str | None:
        return next((r for p, r in self.relations.items() if action_matches(p, action)), None)

    def object_type(self, resource: str) -> str:
        return self.object_types.get(resource, resource)

    def object(self, resource: str, entity_id: Any) -> str:
        return f"{self.object_type(resource)}:{safe_id(entity_id)}"

    def user_ref(self, principal_id: str) -> str:
        """The OpenFGA user for a ``Principal.id`` (``oidc:ann`` → ``user:oidc|ann``):
        use it in ``TupleSync`` relations and when writing tuples yourself."""
        return f"{self.user_type}:{safe_id(principal_id)}"

    def user(self, subject: Mapping[str, Any]) -> str:
        return self.user_ref(subject["id"])

    def _context_tuples(self, subject: Mapping[str, Any]) -> list[ClientTuple]:
        if self.role_type is None:
            return []
        user = self.user(subject)
        return [
            ClientTuple(user=user, relation="member", object=f"{self.role_type}:{role}")
            for role in subject.get("roles") or ()
        ]

    # ---------------------------------------------------------------- decisions

    async def evaluate(self, request: AuthzRequest) -> Any:
        relation = self.relation(request.action)
        if request.subject is None or relation is None or request.resource_type is None:
            return False
        user = self.user(request.subject)
        tuples = self._context_tuples(request.subject) or None
        if any(action_matches(p, request.action) for p in self.collection_actions):
            target = self.object(request.resource_type, self.collection_id)
            return await self._check(user, relation, target, tuples)
        if request.resource is not ...:
            target = self.object(request.resource_type, request.entity_id)
            return await self._check(user, relation, target, tuples)
        key = {"attr": f"resource.{request.id_field}"}
        if not request.filter:  # decided on the record (step 5b)
            return {"exists": key}
        ids = await self._list(user, relation, request.resource_type, tuples)
        return {"in": [key, ids]}

    async def _check(
        self, user: str, relation: str, target: str, tuples: list[ClientTuple] | None
    ) -> bool:
        try:
            response = await self.client.check(
                ClientCheckRequest(
                    user=user, relation=relation, object=target, contextual_tuples=tuples
                )
            )
        except Exception as exc:  # the SDK raises its own exception hierarchy
            raise RemoteCallFailed(
                f"OpenFGA check failed: {exc}", service="openfga", operation="check"
            ) from exc
        return bool(response.allowed)

    async def _list(
        self, user: str, relation: str, resource: str, tuples: list[ClientTuple] | None
    ) -> list[Any]:
        kind = self.object_type(resource)
        try:
            response = await self.client.list_objects(
                ClientListObjectsRequest(
                    user=user, relation=relation, type=kind, contextual_tuples=tuples
                )
            )
        except Exception as exc:
            raise RemoteCallFailed(
                f"OpenFGA list_objects failed: {exc}", service="openfga", operation="list_objects"
            ) from exc
        parse = self._id_types.get(resource, str)
        ids = []
        for name in response.objects or ():
            raw = name.partition(":")[2].replace("|", ":")
            if raw == self.collection_id:
                continue
            try:
                ids.append(parse(raw))
            except (TypeError, ValueError):
                continue
        if len(ids) > self.max_objects:
            raise ConditionNotSupported(
                f"{len(ids)} {resource} are reachable through {relation!r}; the row filter "
                f"is limited to {self.max_objects}",
                hint="Grant listing through a system role, or raise max_objects.",
            )
        return ids
