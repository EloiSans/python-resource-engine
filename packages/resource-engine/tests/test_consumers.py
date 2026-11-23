from __future__ import annotations

import json

import pytest
from invoices.bootstrap import registry
from resource_engine import ResourceManager
from resource_engine.errors import InboxNotSupported, MessageRejected, UndecodableMessage
from resource_engine.messaging import (
    EventRouter,
    MessageConsumer,
    MessageProcessor,
    OutboxDelivery,
    OutboxRelay,
    Outcome,
    RetryPolicy,
    decode_body,
    decode_http,
    to_cloudevent,
)
from resource_engine.ports.messaging import ReceivedMessage
from resource_engine.testing import InMemoryBlobStore, InMemoryQueue, InMemoryStore
from resource_engine.testing.contracts import (
    InboxContract,
    MessageSourceContract,
    contract_envelope,
)


class TestInMemoryQueue(MessageSourceContract):
    @pytest.fixture
    def queue(self):
        return InMemoryQueue()

    @pytest.fixture
    def message_source(self, queue):
        return queue

    @pytest.fixture
    def send(self, queue):
        return queue.publish


class TestInMemoryInbox(InboxContract):
    @pytest.fixture
    def contract_uow_factory(self):
        return InMemoryStore().unit_of_work


def consumer(handler, queue, **kw):
    return MessageConsumer(queue, MessageProcessor(handler, name="tests", **kw), wait_seconds=0.05)


async def test_outbox_to_consumer_end_to_end_with_inbox():
    """Billing publishes through the outbox; a second service reacts to the events."""
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        event_delivery=OutboxDelivery(),
        blob_store=InMemoryBlobStore(),
    )
    queue = InMemoryQueue()
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", inv.id, "submit")
    await OutboxRelay(store.unit_of_work, queue).run_once()

    seen: list[str] = []
    router = EventRouter()
    router.register("ResourceCreated", lambda env: seen.append(f"created {env.key}"))
    router.register("TransitionExecuted", lambda env: seen.append(env.payload["target"]))
    worker = consumer(router, queue, inbox=InMemoryStore().unit_of_work)

    stats = await worker.run_once()
    assert seen == [f"created invoices:{inv.id}", "pending"]
    assert stats.acked == 2 and queue.pending == 0

    # the relay delivering again (at-least-once) is absorbed by the inbox
    await queue.publish([r["envelope"] for r in store.outbox_records()])
    stats = await worker.run_once()
    assert stats.duplicates == 2 and len(seen) == 2


async def test_failures_retry_with_backoff_then_succeed():
    queue = InMemoryQueue()
    await queue.publish([contract_envelope(1)])
    calls = {"n": 0}

    async def flaky(env):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("downstream busy")

    worker = consumer(flaky, queue, retry=RetryPolicy(base_delay=0))
    assert (await worker.run_once()).retried == 1
    assert (await worker.run_once()).retried == 1
    assert (await worker.run_once()).acked == 1
    assert queue.pending == 0 and calls["n"] == 3


async def test_delivery_limit_dead_letters():
    queue = InMemoryQueue()
    await queue.publish([contract_envelope(1)])

    def always_fails(env):
        raise RuntimeError("bug")

    worker = consumer(always_fails, queue, retry=RetryPolicy(max_deliveries=2, base_delay=0))
    await worker.run_once()
    stats = await worker.run_once()
    assert stats.dead_lettered == 1 and queue.pending == 0
    assert queue.dead_letters[0][1] == "RuntimeError: bug"


@pytest.mark.parametrize(
    "handler",
    [
        EventRouter(),  # no handler for the type
        lambda env: (_ for _ in ()).throw(MessageRejected("invalid order")),
    ],
)
async def test_permanent_errors_dead_letter_immediately(handler):
    queue = InMemoryQueue()
    await queue.publish([contract_envelope(1)])
    stats = await consumer(handler, queue).run_once()
    assert stats.dead_lettered == 1 and stats.retried == 0


async def test_same_key_waits_behind_a_failed_message():
    queue = InMemoryQueue()
    a1, b1, a2 = (contract_envelope(n, key=k) for n, k in [(1, "a"), (2, "b"), (3, "a")])
    await queue.publish([a1, b1, a2])
    handled: list[int] = []

    def handler(env):
        if env.payload["n"] == 1:
            raise ConnectionError("busy")
        handled.append(env.payload["n"])

    stats = await consumer(handler, queue, retry=RetryPolicy(base_delay=0)).run_once()
    assert handled == [2] and stats.retried == 2 and stats.acked == 1


async def test_undecodable_messages_are_dead_lettered():
    processor = MessageProcessor(lambda env: None, name="t")
    result = await processor.process(ReceivedMessage(None, error="not json"))
    assert result.outcome is Outcome.DEAD_LETTER and "not json" in result.error


async def test_inbox_requires_a_unit_of_work_with_inbox():
    class NoInbox:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    processor = MessageProcessor(lambda env: None, name="t", inbox=NoInbox)
    with pytest.raises(InboxNotSupported):
        await processor.process(ReceivedMessage(contract_envelope(1)))


def test_decoders_accept_envelopes_and_cloudevents():
    env = contract_envelope(5)
    assert decode_body(env.to_json()) == env
    structured = json.dumps(to_cloudevent(env, source="/t", type_prefix="x."))
    assert decode_body(structured, type_prefix="x.") == env

    ce = to_cloudevent(env, source="/t")
    headers = {f"ce-{k}": str(v) for k, v in ce.items() if k != "data"}
    assert decode_http(headers, json.dumps(ce["data"]).encode()) == env

    for bad in [b"not json", b"[1,2]", b'{"type": "X"}']:
        with pytest.raises(UndecodableMessage):
            decode_body(bad)
