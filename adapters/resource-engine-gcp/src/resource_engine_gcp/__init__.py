"""Google Cloud adapters for Resource Engine. Install ``resource-engine-gcp[pubsub]``."""

from resource_engine_gcp.pubsub import PubSubMessagePublisher
from resource_engine_gcp.pubsub_source import PubSubMessageSource, decode_pubsub_push

__all__ = [
    "decode_pubsub_push",
    "gcs_token",
    "GcsBlobStore",
    "PubSubMessagePublisher",
    "PubSubMessageSource",
]

#: Imported on first use, so each needs only its own extra.
_LAZY = {
    "GcsBlobStore": "resource_engine_gcp.storage",
    "gcs_token": "resource_engine_gcp.storage",
}


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name in _LAZY:
        import importlib

        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(name)
