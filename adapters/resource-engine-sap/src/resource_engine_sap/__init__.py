"""SAP BTP adapters for Resource Engine.

- ``EventMeshMessagePublisher``: SAP Event Mesh (REST messaging API).
- ``EventMeshMessageSource``: pull consumer for Event Mesh queues.
- ``AdvancedEventMeshPublisher``: SAP Integration Suite, advanced event mesh
  (Solace REST messaging).

Push delivery (Event Mesh webhooks, advanced event mesh REST delivery points) goes
to ``resource_engine_fastapi.build_event_receiver``.
"""

from resource_engine_sap.advanced_event_mesh import AdvancedEventMeshPublisher
from resource_engine_sap.event_mesh import (
    EventMeshMessagePublisher,
    event_mesh_client,
    event_topic,
    httprest_credentials,
)
from resource_engine_sap.event_mesh_source import EventMeshMessageSource

__all__ = [
    "AdvancedEventMeshPublisher",
    "EventMeshMessagePublisher",
    "EventMeshMessageSource",
    "event_mesh_client",
    "event_topic",
    "httprest_credentials",
]
