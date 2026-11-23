# resource-engine-sap

SAP BTP adapters for Resource Engine:

- `EventMeshMessagePublisher`: SAP Event Mesh REST messaging API (topics or queues,
  CloudEvents by default), with `event_mesh_client(service_key)` for OAuth.
- `EventMeshMessageSource`: pull consumer for Event Mesh queues (webhooks go to
  `resource_engine_fastapi.build_event_receiver`).
- `AdvancedEventMeshPublisher`: SAP Integration Suite, advanced event mesh (Solace
  REST messaging).

Extras: `event-mesh`, `advanced-event-mesh`, `all`. See the
[adapter catalogue](../../docs/adapters.md#sap). Part of [Resource Engine](../../README.md).
