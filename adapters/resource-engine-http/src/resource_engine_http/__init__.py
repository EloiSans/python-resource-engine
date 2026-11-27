"""HTTP adapters for Resource Engine.

- ``RestRepository`` / ``rest_unit_of_work_factory``: resources stored in a REST API.
- ``SoapService`` / ``SoapRepository``: SOAP services (extra ``soap``;
  import from ``resource_engine_http.soap``).
- ``WebhookMessagePublisher``: events POSTed to an HTTP endpoint.
- ``OAuth2ClientCredentials``: outbound OAuth 2.0 for any of the above.
"""

from resource_engine_http.audit import HttpAuditExporter
from resource_engine_http.auth import OAuth2ClientCredentials
from resource_engine_http.rest import (
    RemoteUnitOfWork,
    RestEndpoint,
    RestRepository,
    default_list_params,
    default_parse_page,
    rest_unit_of_work_factory,
)
from resource_engine_http.webhook import WebhookMessagePublisher

__all__ = [
    "HttpAuditExporter",
    "OAuth2ClientCredentials",
    "RemoteUnitOfWork",
    "RestEndpoint",
    "RestRepository",
    "WebhookMessagePublisher",
    "default_list_params",
    "default_parse_page",
    "rest_unit_of_work_factory",
]
