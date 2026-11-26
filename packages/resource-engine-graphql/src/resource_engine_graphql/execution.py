"""Running GraphQL requests: per-request context and batching, error format,
depth limit."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Hashable, Mapping
from dataclasses import dataclass, field
from typing import Any

from graphql import (
    FieldNode,
    FragmentSpreadNode,
    GraphQLError,
    GraphQLSchema,
    InlineFragmentNode,
    OperationDefinitionNode,
    ValidationRule,
    specified_rules,
    validate,
)
from resource_engine import DomainError, ResourceEngineError, ResourceManager, ResourceRegistry

from resource_engine_graphql.schema import SchemaBuilder

logger = logging.getLogger("resource_engine.graphql")

DEFAULT_MAX_DEPTH = 10


class BatchLoader:
    """Collects the keys loaded in one tick of the event loop and fetches
    them with one call (``fetch(keys) -> {key: value}``); missing keys are
    ``None``. Results are cached for the request."""

    def __init__(self, fetch: Callable[[list[Any]], Awaitable[Mapping[Any, Any]]]) -> None:
        self.fetch = fetch
        self.cache: dict[Hashable, asyncio.Future[Any]] = {}
        self.queue: list[Hashable] = []

    def load(self, key: Hashable) -> asyncio.Future[Any]:
        future = self.cache.get(key)
        if future is not None:
            return future
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.cache[key] = future
        if not self.queue:
            loop.call_soon(lambda: asyncio.ensure_future(self._dispatch()))
        self.queue.append(key)
        return future

    async def _dispatch(self) -> None:
        keys, self.queue = self.queue, []
        try:
            found = await self.fetch(keys)
        except Exception as exc:  # every waiting field gets the error
            for key in keys:
                if not self.cache[key].done():
                    self.cache[key].set_exception(exc)
            return
        for key in keys:
            if not self.cache[key].done():
                self.cache[key].set_result(found.get(key))


@dataclass
class GraphQLContext:
    """``info.context`` of every resolver: the manager, the caller and the
    request's batch loaders."""

    manager: ResourceManager
    actor: Any = None
    loaders: dict[str, BatchLoader] = field(default_factory=dict)

    def loader(self, resource: str) -> BatchLoader:
        if resource not in self.loaders:

            async def fetch(ids: list[Any]) -> Mapping[Any, Any]:
                return await self.manager.get_many(resource, ids, actor=self.actor)

            self.loaders[resource] = BatchLoader(fetch)
        return self.loaders[resource]


def depth_limit(max_depth: int) -> type[ValidationRule]:
    """A validation rule refusing operations nested deeper than ``max_depth``
    fields (introspection fields excepted)."""

    class DepthLimit(ValidationRule):
        def enter_operation_definition(self, node: OperationDefinitionNode, *_args: Any) -> None:
            depth = self._depth(node.selection_set, 0, set())
            if depth > max_depth:
                self.report_error(
                    GraphQLError(
                        f"The query is nested {depth} levels deep; the limit is {max_depth}",
                        node,
                        extensions={"code": "QUERY_TOO_DEEP"},
                    )
                )

        def _depth(self, selection_set: Any, depth: int, fragments: set[str]) -> int:
            if selection_set is None:
                return depth
            deepest = depth
            for selection in selection_set.selections:
                if isinstance(selection, FieldNode):
                    if selection.name.value.startswith("__"):
                        continue
                    deepest = max(
                        deepest, self._depth(selection.selection_set, depth + 1, fragments)
                    )
                elif isinstance(selection, InlineFragmentNode):
                    deepest = max(deepest, self._depth(selection.selection_set, depth, fragments))
                elif isinstance(selection, FragmentSpreadNode):
                    name = selection.name.value
                    fragment = self.context.get_fragment(name)
                    if fragment is not None and name not in fragments:
                        deepest = max(
                            deepest,
                            self._depth(fragment.selection_set, depth, fragments | {name}),
                        )
            return deepest

    return DepthLimit


def format_error(error: GraphQLError) -> dict[str, Any]:
    """A GraphQL error with the engine's ``code``, ``hint``, ``details`` and
    ``doc`` in ``extensions``. Unexpected exceptions are logged and hidden."""
    original = error.original_error
    data: dict[str, Any] = {"message": error.message}
    if error.locations:
        data["locations"] = [{"line": loc.line, "column": loc.column} for loc in error.locations]
    if error.path:
        data["path"] = list(error.path)
    extensions = dict(error.extensions or {})
    if isinstance(original, ResourceEngineError):
        payload = original.to_dict()
        data["message"] = payload.pop("message")
        extensions.update(payload)
    elif isinstance(original, DomainError):
        payload = original.to_dict()
        data["message"] = payload.pop("message", str(original))
        extensions.update(payload)
    elif original is not None and not isinstance(original, GraphQLError | ValueError | TypeError):
        data["message"] = "Internal error"  # reported by execute()
        extensions = {"code": "INTERNAL"}
    if extensions:
        data["extensions"] = extensions
    return data


async def _report(errors: Any, actor: Any) -> None:
    from resource_engine.reporting import report_error

    for error in errors:
        original = error.original_error
        if original is None or isinstance(original, GraphQLError | ValueError | TypeError):
            continue
        await report_error(
            original,
            where="graphql",
            actor=actor,
            route=".".join(str(p) for p in error.path or ()) or None,
        )


DEFAULT_MAX_COST = 10_000


class RequestError(Exception):
    """A request refused before it runs (cost, persisted queries)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def to_response(self) -> dict[str, Any]:
        return {"errors": [{"message": self.message, "extensions": {"code": self.code}}]}


def query_cost(
    schema: GraphQLSchema,
    document: Any,
    operation_name: str | None = None,
    variables: Mapping[str, Any] | None = None,
) -> int:
    """Each field costs 1 plus its selections; fields with a ``limit`` argument
    (pages) multiply their selections by the limit (its default when not given).
    Introspection is free."""
    from graphql import (
        FragmentDefinitionNode,
        IntValueNode,
        VariableNode,
        get_named_type,
    )

    variables = dict(variables or {})
    fragments = {
        d.name.value: d for d in document.definitions if isinstance(d, FragmentDefinitionNode)
    }
    operations = [d for d in document.definitions if isinstance(d, OperationDefinitionNode)]
    if operation_name:
        operations = [o for o in operations if o.name and o.name.value == operation_name]
    if not operations:
        return 0
    operation = operations[0]

    def limit_of(node: FieldNode, field: Any) -> int:
        argument = next((a for a in node.arguments or () if a.name.value == "limit"), None)
        default = field.args["limit"].default_value
        if argument is None:
            return int(default) if isinstance(default, int) else 1
        value = argument.value
        if isinstance(value, IntValueNode):
            return int(value.value)
        if isinstance(value, VariableNode):
            given = variables.get(value.name.value)
            return int(given) if isinstance(given, int) else int(default or 1)
        return 1

    def cost(selection_set: Any, parent: Any, seen: frozenset[str]) -> int:
        if selection_set is None or parent is None:
            return 0
        total = 0
        for selection in selection_set.selections:
            if isinstance(selection, FieldNode):
                name = selection.name.value
                if name.startswith("__"):
                    continue
                field = getattr(parent, "fields", {}).get(name)
                if field is None:
                    continue
                child = cost(selection.selection_set, get_named_type(field.type), seen)
                factor = limit_of(selection, field) if "limit" in field.args else 1
                total += 1 + factor * child
            elif isinstance(selection, InlineFragmentNode):
                target = parent
                if selection.type_condition is not None:
                    target = schema.get_type(selection.type_condition.name.value) or parent
                total += cost(selection.selection_set, target, seen)
            elif isinstance(selection, FragmentSpreadNode):
                name = selection.name.value
                fragment = fragments.get(name)
                if fragment is not None and name not in seen:
                    target = schema.get_type(fragment.type_condition.name.value) or parent
                    total += cost(fragment.selection_set, target, seen | {name})
        return total

    root = schema.get_root_type(operation.operation)
    return cost(operation.selection_set, root, frozenset())


class PersistedQueries:
    """Automatic persisted queries (Apollo APQ), optionally as an allow-list.

    Clients send ``extensions.persistedQuery.sha256Hash`` instead of the query
    text; the first time, with the text too, which is then cached.

    Args:
        known: Queries registered ahead of time (``{sha256: query}``), for
            example extracted from the front end at build time.
        allow_only: Run only ``known`` queries: arbitrary query text and
            unregistered hashes are refused (``PERSISTED_QUERY_REQUIRED`` /
            ``PERSISTED_QUERY_NOT_FOUND``). Recommended in production for
            first-party clients.
        cache_size: Learned queries kept (least recently used out).
    """

    def __init__(
        self,
        known: Mapping[str, str] | None = None,
        *,
        allow_only: bool = False,
        cache_size: int = 1000,
    ) -> None:
        from collections import OrderedDict

        self.known = dict(known or {})
        self.allow_only = allow_only
        self.cache_size = cache_size
        self.learned: OrderedDict[str, str] = OrderedDict()

    @staticmethod
    def hash(query: str) -> str:
        import hashlib

        return hashlib.sha256(query.encode()).hexdigest()

    def resolve(self, query: str | None, extensions: Mapping[str, Any] | None) -> str:
        persisted = (extensions or {}).get("persistedQuery")
        if not isinstance(persisted, Mapping):
            if self.allow_only:
                raise RequestError(
                    "Only persisted queries are accepted", "PERSISTED_QUERY_REQUIRED"
                )
            if not query:
                raise RequestError("Missing 'query'", "BAD_REQUEST")
            return query
        digest = str(persisted.get("sha256Hash") or "")
        if query:
            if self.hash(query) != digest:
                raise RequestError(
                    "provided sha does not match query", "PERSISTED_QUERY_HASH_MISMATCH"
                )
            if self.allow_only and digest not in self.known:
                raise RequestError("PersistedQueryNotFound", "PERSISTED_QUERY_NOT_FOUND")
            if digest not in self.known:
                self._learn(digest, query)
            return query
        found = self.known.get(digest) or self.learned.get(digest)
        if found is None:
            raise RequestError("PersistedQueryNotFound", "PERSISTED_QUERY_NOT_FOUND")
        if digest in self.learned:
            self.learned.move_to_end(digest)
        return found

    def _learn(self, digest: str, query: str) -> None:
        self.learned[digest] = query
        self.learned.move_to_end(digest)
        while len(self.learned) > self.cache_size:
            self.learned.popitem(last=False)


class GraphQLAdapter:
    """The generated schema and a way to run requests against it.

    Args:
        registry / manager: The application's registry and manager.
        max_depth: Deepest nesting of fields an operation may have.
        max_cost: Highest ``query_cost`` an operation may have.
        hub: An ``EventHub`` fed with committed events: adds the
            ``Subscription`` type (``invoiceChanged``...).
        persisted: ``PersistedQueries`` (automatic persisted queries, or an
            allow-list); ``None`` accepts any query text.
    """

    def __init__(
        self,
        registry: ResourceRegistry,
        manager: ResourceManager,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_cost: int = DEFAULT_MAX_COST,
        hub: Any = None,
        persisted: PersistedQueries | None = None,
    ) -> None:
        self.registry = registry
        self.manager = manager
        self.hub = hub
        self.persisted = persisted
        self.max_cost = max_cost
        self.builder = SchemaBuilder(registry, manager, hub=hub)
        self.schema: GraphQLSchema = self.builder.build()
        self.rules = (*specified_rules, depth_limit(max_depth))

    def sdl(self) -> str:
        """The schema in GraphQL SDL, for clients and code generators."""
        from graphql import print_schema

        return print_schema(self.schema)

    def prepare(
        self,
        query: str | None,
        variables: Mapping[str, Any] | None = None,
        operation_name: str | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> Any:
        """Parse, resolve persisted queries, validate and check the cost.
        Returns the document, or a response dict with the errors."""
        from graphql import GraphQLError, parse

        try:
            text = (
                self.persisted.resolve(query, extensions) if self.persisted is not None else query
            )
        except RequestError as exc:
            return exc.to_response()
        if not text:
            return {"errors": [{"message": "Missing 'query'"}]}
        try:
            document = parse(text)
        except GraphQLError as exc:
            return {"errors": [format_error(exc)]}
        errors = validate(self.schema, document, self.rules)
        if errors:
            return {"errors": [format_error(e) for e in errors]}
        cost = query_cost(self.schema, document, operation_name, variables)
        if cost > self.max_cost:
            return RequestError(
                f"The query costs {cost}; the limit is {self.max_cost}. Ask for smaller "
                "pages or fewer nested lists.",
                "QUERY_TOO_COSTLY",
            ).to_response()
        return document

    def is_subscription(self, document: Any, operation_name: str | None = None) -> bool:
        from graphql import get_operation_ast

        operation = get_operation_ast(document, operation_name)
        return operation is not None and operation.operation.value == "subscription"

    async def execute(
        self,
        query: str | None,
        variables: Mapping[str, Any] | None = None,
        operation_name: str | None = None,
        *,
        actor: Any = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one query or mutation; returns ``{"data": ..., "errors": [...]}``."""
        from graphql import execute

        document = self.prepare(query, variables, operation_name, extensions)
        if isinstance(document, dict):
            return document
        if self.is_subscription(document, operation_name):
            return {"errors": [{"message": "Subscriptions need the WebSocket endpoint"}]}
        result = execute(
            self.schema,
            document,
            context_value=GraphQLContext(self.manager, actor),
            variable_values=dict(variables) if variables else None,
            operation_name=operation_name,
        )
        if inspect.isawaitable(result):
            result = await result
        return await self._response(result, actor)

    async def _response(self, result: Any, actor: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"data": result.data}
        if result.errors:
            await _report(result.errors, actor)
            out["errors"] = [format_error(e) for e in result.errors]
        return out

    async def subscribe(
        self,
        query: str | None,
        variables: Mapping[str, Any] | None = None,
        operation_name: str | None = None,
        *,
        actor: Any = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Responses of an operation: one for a query or mutation, one per event
        for a subscription."""
        from graphql import subscribe

        document = self.prepare(query, variables, operation_name, extensions)
        if isinstance(document, dict):
            yield document
            return
        if not self.is_subscription(document, operation_name):
            yield await self.execute(
                query, variables, operation_name, actor=actor, extensions=extensions
            )
            return
        stream = subscribe(
            self.schema,
            document,
            context_value=GraphQLContext(self.manager, actor),
            variable_values=dict(variables) if variables else None,
            operation_name=operation_name,
        )
        if inspect.isawaitable(stream):
            stream = await stream
        if not hasattr(stream, "__aiter__"):  # an ExecutionResult with errors
            yield await self._response(stream, actor)
            return
        try:
            async for result in stream:
                yield await self._response(result, actor)
        finally:
            closing = getattr(stream, "aclose", None)
            if closing is not None:
                await closing()


__all__ = [
    "BatchLoader",
    "GraphQLAdapter",
    "GraphQLContext",
    "PersistedQueries",
    "RequestError",
    "depth_limit",
    "format_error",
    "query_cost",
]
