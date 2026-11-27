"""SAP BTP adapters for Resource Engine.

- ``EventMeshMessagePublisher``: SAP Event Mesh (REST messaging API).
- ``EventMeshMessageSource``: pull consumer for Event Mesh queues.
- ``AdvancedEventMeshPublisher``: SAP Integration Suite, advanced event mesh
  (Solace REST messaging).
- ``ODataEndpoint`` / ``ODataRepository``: OData v2 and v4 entity sets
  (S/4HANA APIs) as resources.
- ``DestinationService`` / ``destination_client``: BTP destinations by name,
  with the authentication they are configured with.

Push delivery (Event Mesh webhooks, advanced event mesh REST delivery points) goes
to ``resource_engine_fastapi.build_event_receiver``.
"""

from resource_engine_sap.advanced_event_mesh import AdvancedEventMeshPublisher
from resource_engine_sap.destinations import (
    Destination,
    DestinationService,
    binding,
    destination_client,
)
from resource_engine_sap.event_mesh import (
    EventMeshMessagePublisher,
    event_mesh_client,
    event_topic,
    httprest_credentials,
)
from resource_engine_sap.event_mesh_source import EventMeshMessageSource
from resource_engine_sap.odata import ODataEndpoint, ODataRepository, odata_unit_of_work_factory

__all__ = [
    "AdvancedEventMeshPublisher",
    "Destination",
    "DestinationService",
    "ODataEndpoint",
    "ODataRepository",
    "binding",
    "destination_client",
    "odata_unit_of_work_factory",
    "EventMeshMessagePublisher",
    "EventMeshMessageSource",
    "event_mesh_client",
    "event_topic",
    "httprest_credentials",
]
