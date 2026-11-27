# resource-engine-sap

SAP BTP adapters for Resource Engine:

- `EventMeshMessagePublisher`: SAP Event Mesh REST messaging API (topics or queues,
  CloudEvents by default), with `event_mesh_client(service_key)` for OAuth.
- `EventMeshMessageSource`: pull consumer for Event Mesh queues (webhooks go to
  `resource_engine_fastapi.build_event_receiver`).
- `AdvancedEventMeshPublisher`: SAP Integration Suite, advanced event mesh (Solace
  REST messaging).
- `ODataEndpoint` / `ODataRepository`: OData v2 and v4 entity sets (S/4HANA APIs)
  as resources, with `$filter`, counts, CSRF tokens and ETags.
- `DestinationService` / `destination_client`: BTP destinations by name, with the
  authentication they are configured with ([SAP](../../docs/sap.md)).

Extras: `event-mesh`, `advanced-event-mesh`, `odata`, `destinations`, `all`. See the
[adapter catalogue](../../docs/adapters.md#sap). Part of [Resource Engine](../../README.md).
