"""Repositories backed by an external JSON REST API.

Use them when a resource's data lives in another service: the engine keeps the
lifecycle (validation, hooks, state machine, events) and the API keeps the data.

**Not transactional.** Writes reach the remote API as soon as the repository is
called, and ``commit``/``rollback`` cannot undo them. If a hook fails after the
mutation (``after_<op>``, ``before_commit``), the remote change stays. Put checks
in ``before_validate``/``validate``/``before_<op>`` hooks, and prefer idempotent
APIs. ``OutboxDelivery`` is not available (there is no local transaction); use
``AfterCommitDelivery``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal, Self

import httpx
from resource_engine.application.query import ListQuery, Page
from resource_engine.calls import outgoing_call

from resource_engine_http.errors import check_response, transport_failed

type Json = dict[str, Any]


def default_list_params(query: ListQuery) -> dict[str, Any]:
    """Query parameters understood by ``resource-engine-fastapi`` routers."""
    params: dict[str, Any] = {"offset": query.offset, "limit": query.limit, **query.filters}
    if query.order_by:
        params["order_by"] = ",".join(query.order_by)
    return params


def default_parse_page(response: httpx.Response, query: ListQuery) -> tuple[list[Json], int]:
    """Accept ``{"items": [...], "total": n}`` or a bare list.

    For bare lists the total comes from ``X-Total-Count`` when present, otherwise it
    is a lower bound (``offset + len(items)``).
    """
    body = response.json()
    if isinstance(body, list):
        total = response.headers.get("x-total-count")
        return body, int(total) if total is not None else query.offset + len(body)
    items = body.get("items", body.get("data", []))
    return items, int(body.get("total", len(items)))


@dataclass(frozen=True)
class RestEndpoint[E]:
    """How one resource maps onto a REST collection.

    Args:
        path: Collection path relative to the client's ``base_url`` (``"/invoices"``).
        to_entity: Builds an entity from a JSON object returned by the API.
        to_payload: Builds the JSON body sent on create and update.
        id_field: Entity attribute that holds the remote id.
        item_path: Item path template; ``{path}`` and ``{id}`` are substituted.
        update_method: ``PATCH`` (default, as ``resource-engine-fastapi`` routers
            expect) or ``PUT``. The body is always the full payload.
        list_params: ``ListQuery`` to query-string parameters.
        parse_page: Response to ``(items, total)``.
        service: Name used in error messages (``RE-E501``).
    """

    path: str
    to_entity: Callable[[Json], E]
    to_payload: Callable[[E], Json]
    id_field: str = "id"
    item_path: str = "{path}/{id}"
    update_method: Literal["PUT", "PATCH"] = "PATCH"
    list_params: Callable[[ListQuery], Mapping[str, Any]] = default_list_params
    parse_page: Callable[[httpx.Response, ListQuery], tuple[list[Json], int]] = default_parse_page
    service: str = "rest"

    @classmethod
    def for_dataclass(cls, model: type[E], path: str, **options: Any) -> RestEndpoint[E]:
        """Endpoint for a dataclass entity whose fields match the JSON keys.

        The id is left out of the create body while it is ``None``; unknown keys in
        responses are ignored.
        """
        id_field = options.get("id_field", "id")
        names = {f.name for f in dataclasses.fields(model)}  # type: ignore[arg-type]

        def to_entity(data: Json) -> E:
            return model(**{k: v for k, v in data.items() if k in names})

        def to_payload(entity: E) -> Json:
            data = dataclasses.asdict(entity)  # type: ignore[call-overload]
            if data.get(id_field) is None:
                data.pop(id_field, None)
            return data

        options.setdefault("to_entity", to_entity)
        options.setdefault("to_payload", to_payload)
        return cls(path=path, **options)


class RestRepository[E]:
    """``Repository`` over a REST collection, using a caller-owned ``httpx.AsyncClient``.

    ``get`` returns ``None`` on 404 and ``delete`` treats 404 as already deleted.
    Any other non-2xx response, or a transport error, raises ``RemoteCallFailed``.
    """

    def __init__(self, client: httpx.AsyncClient, endpoint: RestEndpoint[E]) -> None:
        self.client = client
        self.endpoint = endpoint

    def _item(self, entity_id: Any) -> str:
        return self.endpoint.item_path.format(path=self.endpoint.path, id=entity_id)

    async def _send(self, operation: str, method: str, url: str, **kw: Any) -> httpx.Response:
        service = self.endpoint.service
        full = str(self.client.base_url.join(url)) if self.client.base_url else url
        async with outgoing_call(service, operation, method=method, url=full) as call:
            try:
                response = await self.client.request(method, url, **kw)
            except httpx.HTTPError as exc:
                raise transport_failed(exc, service=service, operation=operation) from exc
            call.status = response.status_code
            return response

    def _check(self, response: httpx.Response, operation: str) -> None:
        check_response(response, service=self.endpoint.service, operation=operation)

    def _from_response(self, response: httpx.Response, fallback: E) -> E:
        if response.status_code == 204 or not response.content:
            return fallback
        return self.endpoint.to_entity(response.json())

    async def get(self, entity_id: Any) -> E | None:
        response = await self._send("get", "GET", self._item(entity_id))
        if response.status_code == 404:
            return None
        self._check(response, "get")
        return self.endpoint.to_entity(response.json())

    async def list(self, query: ListQuery) -> Page[E]:
        response = await self._send(
            "list", "GET", self.endpoint.path, params=dict(self.endpoint.list_params(query))
        )
        self._check(response, "list")
        items, total = self.endpoint.parse_page(response, query)
        return Page([self.endpoint.to_entity(i) for i in items], total, query.offset, query.limit)

    async def add(self, entity: E) -> E:
        response = await self._send(
            "create", "POST", self.endpoint.path, json=self.endpoint.to_payload(entity)
        )
        self._check(response, "create")
        created = self._from_response(response, entity)
        # Keep the caller's object in sync: hooks may still hold a reference to it.
        setattr(entity, self.endpoint.id_field, getattr(created, self.endpoint.id_field))
        return created

    async def update(self, entity: E) -> E:
        entity_id = getattr(entity, self.endpoint.id_field)
        response = await self._send(
            "update",
            self.endpoint.update_method,
            self._item(entity_id),
            json=self.endpoint.to_payload(entity),
        )
        self._check(response, "update")
        return self._from_response(response, entity)

    async def delete(self, entity: E) -> None:
        response = await self._send(
            "delete", "DELETE", self._item(getattr(entity, self.endpoint.id_field))
        )
        if response.status_code != 404:
            self._check(response, "delete")


@dataclass
class RemoteUnitOfWork:
    """Unit of work for remote repositories (REST, SOAP).

    It only hands out repositories: ``commit`` and ``rollback`` do nothing because
    remote writes are applied immediately (see the module docstring). There is no
    ``outbox()``, so ``OutboxDelivery`` fails fast with RE-E403.

    Args:
        repositories: Resource name to a factory returning its repository.
    """

    repositories: Mapping[str, Callable[[], Any]]
    transactional: bool = field(default=False, init=False)
    _cache: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def repository(self, resource_name: str) -> Any:
        if resource_name not in self._cache:
            try:
                factory = self.repositories[resource_name]
            except KeyError:
                raise KeyError(
                    f"No remote repository configured for resource {resource_name!r}"
                ) from None
            self._cache[resource_name] = factory()
        return self._cache[resource_name]

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def __aenter__(self) -> Self:
        self._cache = {}
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def rest_unit_of_work_factory(
    client: httpx.AsyncClient, endpoints: Mapping[str, RestEndpoint[Any]]
) -> Callable[[], RemoteUnitOfWork]:
    """``uow_factory`` for ``ResourceManager`` serving each resource from its endpoint."""
    factories = {name: (lambda ep=ep: RestRepository(client, ep)) for name, ep in endpoints.items()}
    return lambda: RemoteUnitOfWork(factories)
