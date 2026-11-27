"""Azure adapters for Resource Engine. Install ``resource-engine-azure[servicebus]``."""

from resource_engine_azure.servicebus import ServiceBusMessagePublisher
from resource_engine_azure.servicebus_source import ServiceBusMessageSource

__all__ = [
    "AzureBlobStore",
    "EventGridPublisher",
    "ServiceBusMessagePublisher",
    "ServiceBusMessageSource",
]

#: Imported on first use, so each needs only its own extra.
_LAZY = {
    "AzureBlobStore": "resource_engine_azure.blob",
    "EventGridPublisher": "resource_engine_azure.eventgrid",
}


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name in _LAZY:
        import importlib

        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(name)
