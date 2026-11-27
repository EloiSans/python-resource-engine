"""OData v2/v4 repositories against an in-process fake gateway: the repository
contract, $filter/$orderby/paging/count, CSRF tokens, ETags and values."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from resource_engine.errors import ConditionNotSupported, VersionConflict
from resource_engine.testing.contracts import CONTRACT_RESOURCE, ContractItem, RepositoryContract
from resource_engine_sap.odata import (
    FilterCompiler,
    ODataEndpoint,
    ODataRepository,
    literal,
    odata_unit_of_work_factory,
    read_value,
)

ROOT = "/sap/opu/odata/sap/API_ITEMS"
PROPERTIES = {"id": "ItemID", "name": "Name", "rank": "Rank", "group": "Group"}


class FakeGateway:
    """Just enough of an SAP gateway: CSRF, keys, $filter (eq/and), paging, ETags."""

    def __init__(self, version: str) -> None:
        self.version = version
        self.rows: dict[int, dict] = {}
        self.etags: dict[int, int] = {}
        self.next_id = 1
        self.token = "t1"
        self.requests: list[httpx.Request] = []

    def entity(self, key: int) -> dict:
        row = dict(self.rows[key])
        if self.version == "v2":
            row["__metadata"] = {"etag": f'W/"{self.etags[key]}"'}
        else:
            row["@odata.etag"] = f'W/"{self.etags[key]}"'
        return row

    def wrap(self, body):
        return {"d": body} if self.version == "v2" else body

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        if request.method == "GET" and request.headers.get("x-csrf-token") == "Fetch":
            return httpx.Response(200, headers={"x-csrf-token": self.token}, json={})
        if request.method != "GET" and request.headers.get("x-csrf-token") != self.token:
            return httpx.Response(403, headers={"x-csrf-token": "Required"})
        match = re.fullmatch(rf"{ROOT}/Items(?:\((\d+)\))?", url.path)
        assert match, url.path
        key = int(match.group(1)) if match.group(1) else None
        if key is None and request.method == "GET":
            return self.collection(params)
        if key is None:  # POST
            body = json.loads(request.content)
            new = self.next_id
            self.next_id += 1
            self.rows[new] = {**body, "ItemID": new}
            self.etags[new] = 1
            return httpx.Response(201, json=self.wrap(self.entity(new)))
        if key not in self.rows:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        if request.method == "GET":
            return httpx.Response(200, json=self.wrap(self.entity(key)))
        match_header = request.headers.get("if-match")
        if match_header not in ("*", f'W/"{self.etags[key]}"'):
            return httpx.Response(412, json={"error": {"message": "etag"}})
        if request.method == "PATCH":
            self.rows[key].update(json.loads(request.content))
            self.etags[key] += 1
            return httpx.Response(204, headers={"etag": f'W/"{self.etags[key]}"'})
        del self.rows[key]
        return httpx.Response(204)

    def collection(self, params) -> httpx.Response:
        rows = [self.entity(k) for k in self.rows]
        for part in filter(None, (params.get("$filter") or "").split(" and ")):
            prop, op, value = part.split(" ", 2)
            assert op == "eq", part
            wanted = value.strip("'") if value.startswith("'") else int(value)
            rows = [r for r in rows if r.get(prop) == wanted]
        for order in reversed(
            (params.get("$orderby") or "").split(",") if params.get("$orderby") else []
        ):
            prop, direction = order.split(" ")
            rows.sort(key=lambda r: r[prop], reverse=direction == "desc")
        total = len(rows)
        page = rows[
            int(params.get("$skip", 0)) : int(params.get("$skip", 0)) + int(params.get("$top", 50))
        ]
        if self.version == "v2":
            assert params.get("$inlinecount") == "allpages" and params.get("$format") == "json"
            return httpx.Response(200, json={"d": {"results": page, "__count": str(total)}})
        assert params.get("$count") == "true"
        return httpx.Response(200, json={"value": page, "@odata.count": total})


def endpoint(version: str) -> ODataEndpoint:
    return ODataEndpoint.for_dataclass(
        ContractItem, f"{ROOT}/Items", version=version, properties=PROPERTIES
    )


@pytest.fixture(params=["v2", "v4"])
def gateway(request):
    gateway = FakeGateway(request.param)
    client = httpx.AsyncClient(
        base_url="https://s4.example.com", transport=httpx.MockTransport(gateway.handler)
    )
    return gateway, client


class TestODataRepository(RepositoryContract):
    @pytest.fixture
    def contract_uow_factory(self, gateway):
        fake, client = gateway
        return odata_unit_of_work_factory(client, {CONTRACT_RESOURCE: endpoint(fake.version)})

    @pytest.mark.skip(reason="RemoteUnitOfWork is not transactional by design")
    async def test_leaving_without_commit_rolls_back(self): ...

    @pytest.mark.skip(reason="RemoteUnitOfWork is not transactional by design")
    async def test_exception_rolls_back(self): ...


async def test_csrf_token_is_fetched_once_and_renewed(gateway):
    fake, client = gateway
    repo = ODataRepository(client, endpoint(fake.version))
    await repo.add(ContractItem("a", 1))
    await repo.add(ContractItem("b", 2))
    fetches = [r for r in fake.requests if r.headers.get("x-csrf-token") == "Fetch"]
    assert len(fetches) == 1 and str(fetches[0].url).endswith(f"{ROOT}/")
    fake.token = "t2"  # the session expired
    await repo.add(ContractItem("c", 3))
    assert len([r for r in fake.requests if r.headers.get("x-csrf-token") == "Fetch"]) == 2
    assert len(fake.rows) == 3


async def test_etags_guard_updates_and_deletes(gateway):
    fake, client = gateway
    repo = ODataRepository(client, endpoint(fake.version))
    item = await repo.add(ContractItem("a", 1))
    loaded = await repo.get(item.id)
    other = ODataRepository(client, endpoint(fake.version))
    theirs = await other.get(item.id)
    theirs.name = "theirs"
    await other.update(theirs)
    loaded.name = "mine"
    with pytest.raises(VersionConflict):
        await repo.update(loaded)
    assert fake.rows[item.id]["Name"] == "theirs"
    sent = [r for r in fake.requests if r.method == "PATCH"]
    assert sent[0].headers["if-match"] == 'W/"1"'
    if fake.version == "v4":
        assert "IEEE754Compatible=true" in sent[0].headers["content-type"]
    assert "ItemID" not in json.loads(sent[0].content)  # the key is in the URL


def test_filters_compile_per_version():
    v2 = FilterCompiler("v2", {"name": "Name", "amount": "Amount", "on": "Date"})
    v4 = FilterCompiler("v4", v2.properties)
    name, amount = {"attr": "resource.name"}, {"attr": "resource.amount"}
    condition = {"and": [{"in": [name, ["a", "b'c"]]}, {"not": {"lt": [amount, Decimal("10.5")]}}]}
    assert v2.condition(condition) == (
        "((Name eq 'a' or Name eq 'b''c') and not (Amount lt 10.5M))"
    )
    assert v4.condition(condition) == "(Name in ('a','b''c') and not (Amount lt 10.5))"
    assert v4.condition({"gt": [3, amount]}) == "Amount lt 3"
    assert v2.condition({"exists": name}) == "Name ne null"
    assert literal(date(2026, 3, 1), "v2") == "datetime'2026-03-01T00:00:00'"
    assert literal(datetime(2026, 3, 1, 9, tzinfo=UTC), "v4") == "2026-03-01T09:00:00Z"
    with pytest.raises(ConditionNotSupported):
        v4.condition({"eq": [{"attr": "resource.unmapped"}, 1]})


@dataclass
class Invoice:
    number: str
    amount: Decimal
    issued: date
    posted_at: datetime | None = None
    id: str | None = None


def test_v2_values_round_trip():
    ep = ODataEndpoint.for_dataclass(
        Invoice,
        "/Invoices",
        version="v2",
        properties={
            "id": "InvoiceID",
            "number": "Number",
            "amount": "Amount",
            "issued": "Issued",
            "posted_at": "PostedAt",
        },
        read_only={"posted_at"},
    )
    entity = ep.to_entity(
        {
            "__metadata": {"etag": "x"},
            "InvoiceID": "9",
            "Number": "F-1",
            "Amount": "12.50",
            "Issued": "/Date(1772323200000)/",
            "PostedAt": "/Date(1772355600000+0000)/",
        }
    )
    assert entity == Invoice(
        "F-1", Decimal("12.50"), date(2026, 3, 1), datetime(2026, 3, 1, 9, tzinfo=UTC), "9"
    )
    assert ep.to_payload(entity) == {
        "InvoiceID": "9",
        "Number": "F-1",
        "Amount": "12.50",
        "Issued": "/Date(1772323200000)/",
    }
    assert read_value("/Date(-1000)/") == datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC)
