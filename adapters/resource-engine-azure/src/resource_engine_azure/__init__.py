"""Azure adapters for Resource Engine: Service Bus, Blob Storage, Event Grid and
Cosmos DB. Install the extras you use: ``resource-engine-azure[servicebus,blob,cosmos]``."""

from resource_engine_azure.servicebus import ServiceBusMessagePublisher
from resource_engine_azure.servicebus_source import ServiceBusMessageSource

__all__ = [
    "AzureBlobStore",
    "CosmosContainer",
    "CosmosStore",
    "EventGridPublisher",
    "ServiceBusMessagePublisher",
    "ServiceBusMessageSource",
]

#: Imported on first use, so each needs only its own extra.
_LAZY = {
    "AzureBlobStore": "resource_engine_azure.blob",
    "CosmosContainer": "resource_engine_azure.cosmos",
    "CosmosStore": "resource_engine_azure.cosmos",
    "EventGridPublisher": "resource_engine_azure.eventgrid",
}


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name in _LAZY:
        import importlib

        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(name)
