"""SOAP services through `zeep <https://docs.python-zeep.org>`_ (extra ``soap``).

``SoapService`` calls operations and returns plain dicts, mapping faults and
transport errors to ``RemoteCallFailed`` (RE-E501). Use it from hooks and actions
(register it in ``ResourceManager(services=...)``), or wrap it in a
``SoapRepository`` when a resource is stored in a SOAP system.

Like the REST repository, ``SoapRepository`` is not transactional; see
``resource_engine_http.rest``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from resource_engine.application.query import ListQuery, Page
from resource_engine.errors import OperationNotEnabled, RemoteCallFailed

try:
    import zeep
    from zeep.exceptions import Fault, TransportError
    from zeep.helpers import serialize_object
    from zeep.transports import AsyncTransport
except ImportError as exc:  # pragma: no cover
    raise ImportError("SOAP support needs the 'soap' extra: resource-engine-http[soap]") from exc

from resource_engine_http.errors import transport_failed


def _plain(value: Any) -> Any:
    """zeep objects (and OrderedDicts) to plain dicts and lists."""
    value = serialize_object(value, dict)
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


class SoapService:
    """Async calls to one SOAP service.

    Args:
        client: A ``zeep.AsyncClient`` (see ``from_wsdl``).
        name: Name used in error messages.
        binding: ``(service_binding_qname, address)`` to override the endpoint
            declared in the WSDL, e.g. to point at a test or on-premise host.
    """

    def __init__(
        self,
        client: zeep.AsyncClient,
        *,
        name: str = "soap",
        binding: tuple[str, str] | None = None,
    ) -> None:
        self.client = client
        self.name = name
        self._service = client.create_service(*binding) if binding else client.service

    @classmethod
    def from_wsdl(
        cls,
        wsdl: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        wsdl_client: httpx.Client | None = None,
        name: str = "soap",
        binding: tuple[str, str] | None = None,
        settings: zeep.Settings | None = None,
    ) -> SoapService:
        """Load the WSDL (URL or local path) and build the service.

        ``http_client`` carries operation calls (auth, timeouts, TLS);
        ``wsdl_client`` fetches the WSDL and its imports. Loading the WSDL is
        synchronous, so do it at startup.
        """
        transport = AsyncTransport(client=http_client, wsdl_client=wsdl_client)
        client = zeep.AsyncClient(wsdl, transport=transport, settings=settings)
        return cls(client, name=name, binding=binding)

    async def call(self, operation: str, /, **arguments: Any) -> Any:
        """Call ``operation`` and return the result as plain dicts/lists/scalars."""
        try:
            method = getattr(self._service, operation)
        except AttributeError:
            raise OperationNotEnabled(
                f"SOAP service {self.name!r} has no operation {operation!r}",
                hint="Check the operation name against the WSDL (python -m zeep <wsdl>).",
            ) from None
        try:
            result = await method(**arguments)
        except Fault as fault:
            raise RemoteCallFailed(
                f"{self.name} {operation} returned a SOAP fault: {fault.message}",
                service=self.name,
                operation=operation,
                body=f"{fault.code}: {fault.message}",
            ) from fault
        except TransportError as exc:
            raise RemoteCallFailed(
                f"{self.name} {operation} returned HTTP {exc.status_code}",
                service=self.name,
                operation=operation,
                status=exc.status_code,
                body=exc.content.decode(errors="replace") if exc.content else None,
            ) from exc
        except httpx.HTTPError as exc:
            raise transport_failed(exc, service=self.name, operation=operation) from exc
        return _plain(result)

    async def close(self) -> None:
        await self.client.transport.aclose()


type Arguments = dict[str, Any]


@dataclass(frozen=True)
class SoapOperations[E]:
    """Maps repository calls to SOAP operations. Leave an operation as ``None`` if
    the service does not offer it (calling it raises RE-E003).

    Each operation is ``(operation_name, build_arguments)``.

    Args:
        to_entity: Result object (plain dict) to entity.
        get: ``(name, lambda entity_id: {...})``; a ``None`` result means not found.
        list: ``(name, lambda query: {...})``.
        parse_list: List result to ``(items, total)``.
        add: ``(name, lambda entity: {...})``; returns the created object.
        update: ``(name, lambda entity: {...})``; returns the updated object or ``None``.
        delete: ``(name, lambda entity: {...})``.
        is_not_found: Tells whether a ``RemoteCallFailed`` from ``get`` means
            "not found" (services often answer with a fault).
        id_field: Entity attribute holding the remote id.
    """

    to_entity: Callable[[Any], E]
    get: tuple[str, Callable[[Any], Arguments]] | None = None
    list: tuple[str, Callable[[ListQuery], Arguments]] | None = None
    parse_list: Callable[[Any], tuple[list[Any], int]] = lambda r: (r["items"], r["total"])
    add: tuple[str, Callable[[E], Arguments]] | None = None
    update: tuple[str, Callable[[E], Arguments]] | None = None
    delete: tuple[str, Callable[[E], Arguments]] | None = None
    is_not_found: Callable[[RemoteCallFailed], bool] = lambda exc: False
    id_field: str = "id"


class SoapRepository[E]:
    """``Repository`` backed by SOAP operations (see ``SoapOperations``)."""

    def __init__(self, service: SoapService, operations: SoapOperations[E]) -> None:
        self.service = service
        self.ops = operations

    def _op(self, kind: str) -> tuple[str, Callable[[Any], Arguments]]:
        op = getattr(self.ops, kind)
        if op is None:
            raise OperationNotEnabled(
                f"SOAP repository over {self.service.name!r} has no {kind!r} operation",
                hint=f"Set SoapOperations({kind}=('<OperationName>', build_arguments)).",
            )
        return op

    async def get(self, entity_id: Any) -> E | None:
        name, build = self._op("get")
        try:
            result = await self.service.call(name, **build(entity_id))
        except RemoteCallFailed as exc:
            if self.ops.is_not_found(exc):
                return None
            raise
        return None if result is None else self.ops.to_entity(result)

    async def list(self, query: ListQuery) -> Page[E]:
        name, build = self._op("list")
        items, total = self.ops.parse_list(await self.service.call(name, **build(query)))
        return Page([self.ops.to_entity(i) for i in items or []], total, query.offset, query.limit)

    async def add(self, entity: E) -> E:
        name, build = self._op("add")
        created = self.ops.to_entity(await self.service.call(name, **build(entity)))
        setattr(entity, self.ops.id_field, getattr(created, self.ops.id_field))
        return created

    async def update(self, entity: E) -> E:
        name, build = self._op("update")
        result = await self.service.call(name, **build(entity))
        return entity if result is None else self.ops.to_entity(result)

    async def delete(self, entity: E) -> None:
        name, build = self._op("delete")
        await self.service.call(name, **build(entity))
