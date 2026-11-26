"""Running GraphQL requests: per-request context and batching, error format,
depth limit."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Hashable, Mapping
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
    graphql,
    specified_rules,
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
        logger.error("GraphQL resolver failed", exc_info=original)
        data["message"] = "Internal error"
        extensions = {"code": "INTERNAL"}
    if extensions:
        data["extensions"] = extensions
    return data


class GraphQLAdapter:
    """The generated schema and a way to run requests against it.

    Args:
        registry / manager: The application's registry and manager.
        max_depth: Deepest nesting of fields an operation may have.
    """

    def __init__(
        self,
        registry: ResourceRegistry,
        manager: ResourceManager,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self.registry = registry
        self.manager = manager
        self.builder = SchemaBuilder(registry, manager)
        self.schema: GraphQLSchema = self.builder.build()
        self.rules = (*specified_rules, depth_limit(max_depth))

    def sdl(self) -> str:
        """The schema in GraphQL SDL, for clients and code generators."""
        from graphql import print_schema

        return print_schema(self.schema)

    async def execute(
        self,
        query: str,
        variables: Mapping[str, Any] | None = None,
        operation_name: str | None = None,
        *,
        actor: Any = None,
    ) -> dict[str, Any]:
        """Run one request; returns ``{"data": ..., "errors": [...]}``."""
        context = GraphQLContext(self.manager, actor)
        result = await graphql(
            self.schema,
            query,
            context_value=context,
            variable_values=dict(variables) if variables else None,
            operation_name=operation_name,
            rules=self.rules,
        )
        out: dict[str, Any] = {"data": result.data}
        if result.errors:
            out["errors"] = [format_error(e) for e in result.errors]
        return out


__all__ = ["BatchLoader", "GraphQLAdapter", "GraphQLContext", "depth_limit", "format_error"]
