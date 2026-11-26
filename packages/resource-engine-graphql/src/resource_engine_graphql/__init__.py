"""GraphQL adapter for Resource Engine: a schema generated from the resources.

See docs/graphql.md.
"""

from resource_engine_graphql.execution import (
    BatchLoader,
    GraphQLAdapter,
    GraphQLContext,
    depth_limit,
    format_error,
)
from resource_engine_graphql.http import mount_graphql

__all__ = [
    "BatchLoader",
    "GraphQLAdapter",
    "GraphQLContext",
    "depth_limit",
    "format_error",
    "mount_graphql",
]
