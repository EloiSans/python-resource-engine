# SAP: OData APIs and BTP destinations

Records that live in S/4HANA (business partners, sales orders, products) or
another OData service can be resources. The engine keeps validation, hooks,
state machines, permissions and events; SAP keeps the data. BTP destinations
give the URL and credentials, so the app holds no SAP secrets.

```python
from resource_engine_sap import (
    DestinationService,
    ODataEndpoint,
    binding,
    destination_client,
    odata_unit_of_work_factory,
)


@dataclass
class Partner:
    name: str
    category: str
    created_on: date | None = None
    id: str | None = None


partners = ODataEndpoint.for_dataclass(
    Partner,
    "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner",
    version="v2",
    properties={
        "id": "BusinessPartner",
        "name": "BusinessPartnerFullName",
        "category": "BusinessPartnerCategory",
        "created_on": "CreationDate",
    },
    read_only={"created_on"},
)

destinations = DestinationService(http, binding("destination"))
s4 = await destination_client(destinations, "S4HANA")
manager = ResourceManager(registry, odata_unit_of_work_factory(s4, {"partners": partners}))
```

## OData repositories

`ODataEndpoint.for_dataclass` maps model fields to OData properties. Only the
mapped properties are read (`$select`) and written. `read_only` fields are read
but never sent (values SAP computes).

| | v2 | v4 |
|---|---|---|
| Filters and row filters | `$filter` (`in` as `or`) | `$filter` (`in (...)`) |
| Sorting, paging | `$orderby`, `$skip`, `$top` | same |
| Total | `$inlinecount=allpages` | `$count=true` |
| Dates | `/Date(ms)/` | ISO 8601 |
| Decimals | strings | strings (`IEEE754Compatible=true`) |

- **Row filters** use `eq ne lt le gt ge`, `and`/`or`/`not`, `in`/`nin` and
  `exists` (`ne null`). Others (`contains`, two fields compared) fail closed
  with `ConditionNotSupported` ([RE-E705](errors.md#re-e705)).
- **CSRF:** writes fetch the gateway's `X-CSRF-Token` once per client and
  fetch it again when the gateway answers `403 Required`.
- **ETags:** the ETag read with a record goes back as `If-Match` on update
  and delete (`*` when none was read). A `412` is a `VersionConflict`
  ([RE-E105](errors.md#re-e105)): reload and retry.
- **Keys** are single properties; strings are quoted (`('1000042')`),
  numbers not.
- **Not transactional:** each write is one request, applied at once. To
  combine SAP writes with SQL ones in one operation, use a
  [composite store](composite.md): if the operation fails, the SAP writes are
  compensated (give an inverse per resource when "delete" is wrong, such as
  cancelling an order).

## BTP destinations

`DestinationService` reads destinations from the Destination service, with
its own client credentials. `binding("destination")` reads them from
`VCAP_SERVICES` (Cloud Foundry) or `$SERVICE_BINDING_ROOT` (Kyma).

- **Authentication** comes from the service as ready-made headers
  (`authTokens`): basic, OAuth 2.0 client credentials, SAML bearer, JWT
  bearer... `destination_client` sends them, renews them when they expire and
  asks again once when the target answers `401`.
- **Principal propagation:** pass the caller's token
  (`destination_client(..., user_token=jwt)`); the service exchanges it for
  the destination's user. Destinations are cached per user token.
- **Additional properties** (`sap-client`, custom ones) are in
  `Destination.properties`.
- **On-premise** destinations (`ProxyType: OnPremise`) go through the Cloud
  Connector and the Connectivity service proxy, which this adapter doesn't
  set up. They are refused with a `ConfigurationError`.

## Testing

There is no public SAP emulator. The tests run the repository contract
against an in-process fake gateway (v2 and v4), and check the CSRF, ETag,
value and destination exchanges.
