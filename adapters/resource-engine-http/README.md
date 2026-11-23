# resource-engine-http

HTTP adapters for Resource Engine:

- `RestRepository`: resources stored in a JSON REST API (works out of the box with
  `resource-engine-fastapi` services).
- `SoapService` and `SoapRepository` (extra `soap`, uses zeep).
- `WebhookMessagePublisher`: events POSTed as envelope JSON or CloudEvents.
- `OAuth2ClientCredentials`: outbound OAuth 2.0 for `httpx.AsyncClient`.

Remote repositories are not transactional; read the notes in the
[adapter catalogue](../../docs/adapters.md#remote-repositories-rest-soap).
Part of [Resource Engine](../../README.md).
