"""``OpaEngine``: decisions from a running OPA server."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
from resource_engine.errors import ConditionNotSupported, RemoteCallFailed
from resource_engine.ports.authz import AuthzRequest

from resource_engine_opa.ucast import ucast_condition

UCAST = "application/vnd.opa.ucast.all+json"


def encode(value: Any) -> Any:
    """Plain Python → JSON for OPA: decimals become numbers, dates ISO text."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [encode(v) for v in value]
    return value


class OpaEngine:
    """``PolicyEngine`` over OPA's REST API.

    Args:
        url: OPA base URL (``http://opa:8181``).
        package: Rego package of the decision (``resource_engine`` →
            ``data.resource_engine``).
        rule: Boolean rule deciding requests (``allow``).
        client: Optional ``httpx.AsyncClient`` (auth headers, TLS, pooling).
        headers: Extra headers (e.g. ``Authorization`` for OPA's token auth).

    The input document is ``{subject, action, resource_type, resource, input,
    context}`` (``subject`` is ``null`` for anonymous callers). What is not known
    yet is left out and sent as an unknown to the compile API, whose UCAST answer
    becomes the row filter. Write rules without ``default allow``; see
    docs/authorizers.md#opa for what the compile API accepts.
    """

    def __init__(
        self,
        url: str,
        *,
        package: str = "resource_engine",
        rule: str = "allow",
        client: httpx.AsyncClient | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.path = "/".join([*package.split("."), rule])
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.headers = dict(headers or {})

    def input_document(self, request: AuthzRequest) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "subject": request.subject,
            "action": request.action,
            "resource_type": request.resource_type,
            "context": request.context,
        }
        if request.resource is not ...:
            doc["resource"] = request.resource
        if request.input is not ...:
            doc["input"] = request.input
        return encode(doc)

    async def _post(self, endpoint: str, body: dict[str, Any], **headers: str) -> dict[str, Any]:
        url = f"{self.url}/v1/{endpoint}/{self.path}"
        try:
            response = await self.client.post(url, json=body, headers={**self.headers, **headers})
        except httpx.HTTPError as exc:
            raise RemoteCallFailed(
                f"OPA is not reachable: {exc}", service="opa", operation=endpoint
            ) from exc
        if response.status_code >= 400:
            data = _json(response)
            fragment = [e for e in data.get("errors") or () if e.get("code") == "pe_fragment_error"]
            if fragment:
                raise ConditionNotSupported(
                    f"OPA cannot turn {self.path} into a row filter: {fragment[0].get('message')}",
                    hint="Rewrite the rule with the constructs the compile API accepts "
                    "(docs/authorizers.md#opa), or grant the action without record conditions.",
                    details={"location": fragment[0].get("location")},
                )
            raise RemoteCallFailed(
                f"OPA answered {response.status_code}",
                service="opa",
                operation=endpoint,
                status=response.status_code,
                body=response.text,
            )
        return _json(response)

    async def evaluate(self, request: AuthzRequest) -> Any:
        body: dict[str, Any] = {"input": self.input_document(request)}
        if not request.unknowns:
            return (await self._post("data", body)).get("result") is True
        body["unknowns"] = [f"input.{name}" for name in request.unknowns]
        answer = await self._post("compile", body, accept=UCAST)
        if "result" not in answer or "query" not in (answer["result"] or {}):
            return False
        return ucast_condition(answer["result"]["query"])


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
