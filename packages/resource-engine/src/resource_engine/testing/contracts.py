"""Contract tests that every adapter must pass.

Subclass a contract in your adapter's test suite and provide its fixtures::

    class TestMyRepository(RepositoryContract):
        @pytest.fixture
        def contract_uow_factory(self):
            return my_uow_factory  # serving the resource CONTRACT_RESOURCE

Requires ``pytest`` and ``pytest-asyncio`` (``asyncio_mode = "auto"``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from resource_engine.application.query import ListQuery
from resource_engine.messaging.envelope import EventEnvelope
from resource_engine.ports.messaging import SupportsInbox, SupportsOutbox

CONTRACT_RESOURCE = "contract_items"


@dataclass
class ContractItem:
    """Entity used by the repository contract. Map it in your adapter's tests."""

    name: str
    rank: int
    group: str = "a"
    id: int | None = None


def contract_envelope(n: int = 0, **overrides: Any) -> EventEnvelope:
    data: dict[str, Any] = {
        "type": "ContractEvent",
        "payload": {"n": n, "amount": "12.50", "text": "àèï €"},
        "occurred_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "correlation_id": f"corr-{n}",
        "resource": CONTRACT_RESOURCE,
        "key": f"{CONTRACT_RESOURCE}:{n}",
    }
    data.update(overrides)
    return EventEnvelope(**data)


class UnitOfWorkContract:
    @pytest.fixture
    def contract_uow_factory(self) -> Callable[[], Any]:
        raise NotImplementedError("Provide a unit of work factory serving CONTRACT_RESOURCE")

    async def test_commit_makes_changes_visible(self, contract_uow_factory):
        async with contract_uow_factory() as uow:
            item = await uow.repository(CONTRACT_RESOURCE).add(ContractItem("a", 1))
            await uow.commit()
        async with contract_uow_factory() as uow:
            assert await uow.repository(CONTRACT_RESOURCE).get(item.id) is not None

    async def test_leaving_without_commit_rolls_back(self, contract_uow_factory):
        async with contract_uow_factory() as uow:
            item = await uow.repository(CONTRACT_RESOURCE).add(ContractItem("a", 1))
            item_id = item.id
        async with contract_uow_factory() as uow:
            assert await uow.repository(CONTRACT_RESOURCE).get(item_id) is None

    async def test_exception_rolls_back(self, contract_uow_factory):
        item_id = None
        with pytest.raises(RuntimeError):
            async with contract_uow_factory() as uow:
                item = await uow.repository(CONTRACT_RESOURCE).add(ContractItem("a", 1))
                item_id = item.id
                raise RuntimeError("boom")
        async with contract_uow_factory() as uow:
            assert await uow.repository(CONTRACT_RESOURCE).get(item_id) is None


class RepositoryContract(UnitOfWorkContract):
    async def _seed(self, factory: Callable[[], Any]) -> list[ContractItem]:
        async with factory() as uow:
            repo = uow.repository(CONTRACT_RESOURCE)
            items = [
                await repo.add(ContractItem(name, rank, group))
                for name, rank, group in [("c", 3, "a"), ("a", 1, "b"), ("b", 2, "a")]
            ]
            await uow.commit()
        return items

    async def test_add_assigns_distinct_ids(self, contract_uow_factory):
        items = await self._seed(contract_uow_factory)
        ids = [i.id for i in items]
        assert None not in ids and len(set(ids)) == 3

    async def test_get_missing_returns_none(self, contract_uow_factory):
        async with contract_uow_factory() as uow:
            assert await uow.repository(CONTRACT_RESOURCE).get(999_999) is None

    async def test_update_and_delete(self, contract_uow_factory):
        item = (await self._seed(contract_uow_factory))[0]
        async with contract_uow_factory() as uow:
            repo = uow.repository(CONTRACT_RESOURCE)
            loaded = await repo.get(item.id)
            loaded.name = "renamed"
            await repo.update(loaded)
            await uow.commit()
        async with contract_uow_factory() as uow:
            repo = uow.repository(CONTRACT_RESOURCE)
            loaded = await repo.get(item.id)
            assert loaded.name == "renamed"
            await repo.delete(loaded)
            await uow.commit()
        async with contract_uow_factory() as uow:
            assert await uow.repository(CONTRACT_RESOURCE).get(item.id) is None

    async def test_list_filters_orders_and_pages(self, contract_uow_factory):
        await self._seed(contract_uow_factory)
        async with contract_uow_factory() as uow:
            repo = uow.repository(CONTRACT_RESOURCE)
            page = await repo.list(ListQuery(order_by=["rank"]))
            assert [i.name for i in page.items] == ["a", "b", "c"] and page.total == 3
            page = await repo.list(ListQuery(order_by=["-rank"], limit=2, offset=1))
            assert [i.name for i in page.items] == ["b", "a"] and page.total == 3
            page = await repo.list(ListQuery(filters={"group": "a"}, order_by=["name"]))
            assert [i.name for i in page.items] == ["b", "c"] and page.total == 2


class OutboxContract:
    @pytest.fixture
    def contract_uow_factory(self) -> Callable[[], Any]:
        raise NotImplementedError("Provide a unit of work factory whose units have outbox()")

    async def _pending(self, factory: Callable[[], Any], limit: int = 100) -> list[Any]:
        async with factory() as uow:
            return await uow.outbox().pending(limit)

    async def test_unit_of_work_supports_outbox(self, contract_uow_factory):
        async with contract_uow_factory() as uow:
            assert isinstance(uow, SupportsOutbox)

    async def test_committed_messages_are_pending_in_order(self, contract_uow_factory):
        envelopes = [contract_envelope(n) for n in range(5)]
        async with contract_uow_factory() as uow:
            await uow.outbox().add(envelopes[:3])
            await uow.outbox().add(envelopes[3:])
            await uow.commit()
        pending = await self._pending(contract_uow_factory)
        assert [r.envelope.id for r in pending] == [e.id for e in envelopes]
        assert pending[0].envelope == envelopes[0]
        assert [r.envelope.id for r in await self._pending(contract_uow_factory, 2)] == [
            e.id for e in envelopes[:2]
        ]

    async def test_rolled_back_messages_are_discarded(self, contract_uow_factory):
        async with contract_uow_factory() as uow:
            await uow.outbox().add([contract_envelope(1)])
        assert await self._pending(contract_uow_factory) == []

    async def test_mark_published_and_failed(self, contract_uow_factory):
        a, b = contract_envelope(1), contract_envelope(2)
        async with contract_uow_factory() as uow:
            await uow.outbox().add([a, b])
            await uow.commit()
        async with contract_uow_factory() as uow:
            await uow.outbox().mark_published([a.id])
            await uow.outbox().mark_failed([b.id], "broker down")
            await uow.commit()
        (record,) = await self._pending(contract_uow_factory)
        assert record.envelope.id == b.id
        assert record.attempts == 1 and record.last_error == "broker down"


type Receive = Callable[[int], Awaitable[list[EventEnvelope]]]


class MessagePublisherContract:
    """Round-trip through a real or emulated broker.

    Fixtures:
        message_publisher: the ``MessagePublisher`` under test.
        receive: ``async (expected_count) -> list[EventEnvelope]`` reading back from
            the destination (e.g. the queue subscribed to a topic). It should wait
            until ``expected_count`` messages arrive or a timeout elapses.
    """

    @pytest.fixture
    def message_publisher(self) -> Any:
        raise NotImplementedError

    @pytest.fixture
    def receive(self) -> Receive:
        raise NotImplementedError

    async def test_round_trip_preserves_envelope(self, message_publisher, receive):
        sent = contract_envelope(7)
        await message_publisher.publish([sent])
        (got,) = await receive(1)
        assert got == sent

    async def test_batches_larger_than_broker_limits(self, message_publisher, receive):
        sent = [contract_envelope(n) for n in range(25)]
        await message_publisher.publish(sent)
        got = await receive(25)
        assert sorted(e.id for e in got) == sorted(e.id for e in sent)

    async def test_empty_publish_is_a_no_op(self, message_publisher, receive):
        await message_publisher.publish([])
        await asyncio.sleep(0)


class InboxContract:
    @pytest.fixture
    def contract_uow_factory(self) -> Callable[[], Any]:
        raise NotImplementedError("Provide a unit of work factory whose units have inbox()")

    async def _processed(self, factory, consumer: str, ids: list[str]) -> set[str]:
        async with factory() as uow:
            return await uow.inbox().processed(consumer, ids)

    async def test_unit_of_work_supports_inbox(self, contract_uow_factory):
        async with contract_uow_factory() as uow:
            assert isinstance(uow, SupportsInbox)

    async def test_marks_are_per_consumer_and_committed(self, contract_uow_factory):
        async with contract_uow_factory() as uow:
            await uow.inbox().mark_processed("billing", ["m1", "m2"])
            await uow.commit()
        assert await self._processed(contract_uow_factory, "billing", ["m1", "m3"]) == {"m1"}
        assert await self._processed(contract_uow_factory, "audit", ["m1"]) == set()

    async def test_rolled_back_marks_are_discarded(self, contract_uow_factory):
        async with contract_uow_factory() as uow:
            await uow.inbox().mark_processed("billing", ["m9"])
        assert await self._processed(contract_uow_factory, "billing", ["m9"]) == set()


type Send = Callable[[list[EventEnvelope]], Awaitable[None]]


async def drain(source: Any, expected: int, *, timeout: float = 10.0) -> list[Any]:
    """Receive until ``expected`` messages arrived (or ``timeout``); for broker tests."""
    got: list[Any] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while len(got) < expected and loop.time() < deadline:
        got += await source.receive(min(10, expected - len(got)), 1.0)
    return got


class MessageSourceContract:
    """Behaviour of a ``MessageSource`` against a real or emulated broker.

    Fixtures:
        message_source: the source under test, reading from an empty destination.
        send: ``async (envelopes) -> None`` putting envelopes on that destination
            (usually the matching publisher).
    """

    @pytest.fixture
    def message_source(self) -> Any:
        raise NotImplementedError

    @pytest.fixture
    def send(self) -> Send:
        raise NotImplementedError

    async def test_receive_decodes_envelopes(self, message_source, send):
        sent = [contract_envelope(n) for n in range(3)]
        await send(sent)
        got = await drain(message_source, 3)
        assert sorted((m.envelope for m in got), key=lambda e: e.id) == sorted(
            sent, key=lambda e: e.id
        )
        assert all(m.delivery_count in (1, None) for m in got)
        await message_source.ack(got)

    async def test_acked_messages_are_not_redelivered(self, message_source, send):
        await send([contract_envelope(1)])
        got = await drain(message_source, 1)
        await message_source.ack(got)
        assert await message_source.receive(10, 1.0) == []

    async def test_retried_messages_come_back(self, message_source, send):
        sent = contract_envelope(2)
        await send([sent])
        (first,) = await drain(message_source, 1)
        await message_source.retry([first], 0)
        (again,) = await drain(message_source, 1)
        assert again.envelope == sent
        if first.delivery_count is not None and again.delivery_count is not None:
            assert again.delivery_count == first.delivery_count + 1
        await message_source.ack([again])

    async def test_empty_receive_returns_within_wait(self, message_source):
        loop = asyncio.get_running_loop()
        start = loop.time()
        assert await message_source.receive(10, 1.0) == []
        assert loop.time() - start < 5


class BlobStoreContract:
    """Behaviour of a ``BlobStore`` (and ``SupportsPresignedUrls`` when provided).

    Fixtures:
        blob_store: the store under test, on an empty bucket/prefix.
        presigned_client: ``(upload, download)`` coroutines performing the client
            side of presigned requests (``httpx`` for real stores). Return ``None``
            to skip the presigned tests.
    """

    @pytest.fixture
    def blob_store(self) -> Any:
        raise NotImplementedError

    @pytest.fixture
    def presigned_client(self) -> Any:
        return None

    async def test_put_head_open_round_trip(self, blob_store):
        import hashlib

        data = b"%PDF-1.7 " + bytes(range(256)) * 100
        info = await blob_store.put("a/b/1", data, content_type="application/pdf")
        assert info.size == len(data) and info.sha256 == hashlib.sha256(data).hexdigest()
        head = await blob_store.head("a/b/1")
        assert head is not None and head.size == len(data)
        assert b"".join([c async for c in blob_store.open("a/b/1")]) == data

    async def test_streamed_put(self, blob_store):
        async def chunks():
            for n in range(5):
                yield bytes([n]) * 1000

        info = await blob_store.put("streamed", chunks(), content_type="application/octet-stream")
        assert info.size == 5000

    async def test_max_bytes_aborts_without_leaving_an_object(self, blob_store):
        from resource_engine.errors import DocumentTooLarge

        with pytest.raises(DocumentTooLarge):
            await blob_store.put("big", b"x" * 2000, content_type="text/plain", max_bytes=1000)
        assert await blob_store.head("big") is None

    async def test_missing_and_delete(self, blob_store):
        from resource_engine.errors import BlobNotFound

        assert await blob_store.head("missing") is None
        with pytest.raises(BlobNotFound):
            async for _ in blob_store.open("missing"):
                pass
        await blob_store.put("gone", b"1", content_type="text/plain")
        await blob_store.delete(["gone", "never-existed"])
        assert await blob_store.head("gone") is None

    async def test_list_by_prefix(self, blob_store):
        for key in ("p/1", "p/2", "q/1"):
            await blob_store.put(key, b"x", content_type="text/plain")
        assert sorted([i.key async for i in blob_store.list("p/")]) == ["p/1", "p/2"]

    async def test_presigned_upload_and_download(self, blob_store, presigned_client):
        from datetime import timedelta

        from resource_engine.ports.storage import SupportsPresignedUrls

        if presigned_client is None or not isinstance(blob_store, SupportsPresignedUrls):
            pytest.skip("store has no presigned URLs")
        upload, download = presigned_client
        put = await blob_store.presign_put(
            "direct/1", content_type="text/plain", expires=timedelta(minutes=5)
        )
        await upload(put, b"uploaded directly")
        head = await blob_store.head("direct/1")
        assert head is not None and head.size == len(b"uploaded directly")
        get = await blob_store.presign_get(
            "direct/1", expires=timedelta(minutes=5), download_name="nota.txt"
        )
        assert await download(get) == b"uploaded directly"


class ConditionContract(RepositoryContract):
    """Row filters (``ListQuery.condition``) for repositories with
    ``supports_conditions = True``."""

    async def test_supports_conditions(self, contract_uow_factory):
        async with contract_uow_factory() as uow:
            assert getattr(uow.repository(CONTRACT_RESOURCE), "supports_conditions", False)

    async def test_condition_filters_and_counts(self, contract_uow_factory):
        await self._seed(contract_uow_factory)
        cases = [
            ({"eq": [{"attr": "resource.group"}, "a"]}, ["b", "c"]),
            ({"lt": [{"attr": "resource.rank"}, 3]}, ["a", "b"]),
            ({"in": [{"attr": "resource.name"}, ["a", "c"]]}, ["a", "c"]),
            ({"nin": [{"attr": "resource.name"}, ["a"]]}, ["b", "c"]),
            (
                {
                    "or": [
                        {"eq": [{"attr": "resource.group"}, "b"]},
                        {
                            "and": [
                                {"ge": [{"attr": "resource.rank"}, 3]},
                                {"not": {"eq": [{"attr": "resource.name"}, "x"]}},
                            ]
                        },
                    ]
                },
                ["a", "c"],
            ),
            (False, []),
        ]
        async with contract_uow_factory() as uow:
            repo = uow.repository(CONTRACT_RESOURCE)
            for condition, names in cases:
                page = await repo.list(ListQuery(order_by=["name"], condition=condition))
                assert [i.name for i in page.items] == names, condition
                assert page.total == len(names)
            page = await repo.list(
                ListQuery(filters={"group": "a"}, condition={"gt": [{"attr": "resource.rank"}, 2]})
            )
            assert [i.name for i in page.items] == ["c"]
