"""SOAP repository against a fake SOAP 1.1 server (WSDL in ``items.wsdl``)."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest
from lxml import etree
from resource_engine.errors import OperationNotEnabled, RemoteCallFailed
from resource_engine.testing.contracts import CONTRACT_RESOURCE, ContractItem, RepositoryContract
from resource_engine_http.rest import RemoteUnitOfWork
from resource_engine_http.soap import SoapOperations, SoapRepository, SoapService

WSDL = str(Path(__file__).with_name("items.wsdl"))
NS = {"s": "http://schemas.xmlsoap.org/soap/envelope/", "i": "urn:items"}


def envelope(body: str) -> str:
    return (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:i="urn:items">'
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )


def item_xml(item: dict, tag: str = "item") -> str:
    fields = "".join(
        f"<i:{k}>{escape(str(item[k]))}</i:{k}>" for k in ("id", "name", "rank", "group")
    )
    return f"<i:{tag}>{fields}</i:{tag}>"


class FakeItemsServer:
    """Minimal document/literal service: GetItem, ListItems, SaveItem, DeleteItem."""

    def __init__(self) -> None:
        self.items: dict[int, dict] = {}
        self.next_id = 1
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503, text="maintenance")
        (op,) = etree.fromstring(request.content).find("s:Body", NS)
        name = etree.QName(op).localname
        args = {etree.QName(c).localname: c for c in op}
        body = getattr(self, name)(args)
        if body.startswith("<s:Fault>"):
            return httpx.Response(500, text=envelope(body), headers={"content-type": "text/xml"})
        return httpx.Response(200, text=envelope(body), headers={"content-type": "text/xml"})

    def GetItem(self, args) -> str:
        item = self.items.get(int(args["id"].text))
        if item is None:
            return (
                "<s:Fault><faultcode>s:Client</faultcode>"
                f"<faultstring>Item {args['id'].text} not found</faultstring></s:Fault>"
            )
        return f"<i:GetItemResponse>{item_xml(item)}</i:GetItemResponse>"

    def ListItems(self, args) -> str:
        rows = list(self.items.values())
        if "group" in args:
            rows = [r for r in rows if r["group"] == args["group"].text]
        for key in reversed((args["orderBy"].text or "").split(",") if "orderBy" in args else []):
            field = key.lstrip("-")
            rows.sort(key=lambda r, f=field: r[f], reverse=key.startswith("-"))
        offset, limit = int(args["offset"].text), int(args["limit"].text)
        page = "".join(item_xml(r) for r in rows[offset : offset + limit])
        return f"<i:ListItemsResponse><i:total>{len(rows)}</i:total>{page}</i:ListItemsResponse>"

    def SaveItem(self, args) -> str:
        fields = {etree.QName(c).localname: c.text for c in args["item"]}
        item = {"name": fields["name"], "rank": int(fields["rank"]), "group": fields["group"]}
        item["id"] = int(fields["id"]) if fields.get("id") else self._new_id()
        self.items[item["id"]] = item
        return f"<i:SaveItemResponse>{item_xml(item)}</i:SaveItemResponse>"

    def DeleteItem(self, args) -> str:
        self.items.pop(int(args["id"].text), None)
        return "<i:DeleteItemResponse><i:ok>true</i:ok></i:DeleteItemResponse>"

    def _new_id(self) -> int:
        self.next_id += 1
        return self.next_id - 1


def item_args(item: ContractItem) -> dict:
    return {"item": {"id": item.id, "name": item.name, "rank": item.rank, "group": item.group}}


OPERATIONS = SoapOperations(
    to_entity=lambda r: ContractItem(r["name"], r["rank"], r["group"], id=r["id"]),
    get=("GetItem", lambda entity_id: {"id": entity_id}),
    list=(
        "ListItems",
        lambda q: {
            "offset": q.offset,
            "limit": q.limit,
            "group": q.filters.get("group"),
            "orderBy": ",".join(q.order_by) or None,
        },
    ),
    parse_list=lambda r: (r["item"], r["total"]),
    add=("SaveItem", item_args),
    update=("SaveItem", item_args),
    delete=("DeleteItem", lambda item: {"id": item.id}),
    is_not_found=lambda exc: "not found" in (exc.details.get("body") or ""),
)


@pytest.fixture
def server():
    return FakeItemsServer()


@pytest.fixture
def soap(server):
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return SoapService.from_wsdl(WSDL, http_client=client, name="items")


class TestSoapRepository(RepositoryContract):
    @pytest.fixture
    def contract_uow_factory(self, soap):
        return lambda: RemoteUnitOfWork(
            {CONTRACT_RESOURCE: lambda: SoapRepository(soap, OPERATIONS)}
        )

    @pytest.mark.skip(reason="RemoteUnitOfWork is not transactional by design")
    async def test_leaving_without_commit_rolls_back(self): ...

    @pytest.mark.skip(reason="RemoteUnitOfWork is not transactional by design")
    async def test_exception_rolls_back(self): ...


async def test_call_returns_plain_python(soap, server):
    created = await soap.call("SaveItem", item={"name": "àè €", "rank": 2, "group": "g"})
    assert created == {"id": 1, "name": "àè €", "rank": 2, "group": "g"}
    assert type(created) is dict
    assert (await soap.call("DeleteItem", id=1)) is True


async def test_faults_and_http_errors_become_remote_call_failed(soap, server):
    with pytest.raises(RemoteCallFailed) as info:
        await soap.call("GetItem", id=42)
    assert info.value.code == "RE-E501" and "not found" in info.value.details["body"]

    server.down = True
    with pytest.raises(RemoteCallFailed) as info:
        await soap.call("GetItem", id=42)
    assert info.value.details["status"] == 503


async def test_unknown_and_unmapped_operations(soap):
    with pytest.raises(OperationNotEnabled):
        await soap.call("Nope")
    repo = SoapRepository(soap, SoapOperations(to_entity=dict))
    with pytest.raises(OperationNotEnabled):
        await repo.get(1)
