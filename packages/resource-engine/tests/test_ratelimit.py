"""Rate limits: the sliding window, per-action limits in the manager, exemptions."""

from __future__ import annotations

import pytest
from invoices.bootstrap import registry
from resource_engine import ResourceManager
from resource_engine.authz import system_principal
from resource_engine.errors import RateLimited
from resource_engine.ports.identity import Principal
from resource_engine.ratelimit import (
    InMemoryRateLimitStore,
    Limit,
    SlidingWindowLimiter,
)
from resource_engine.testing import InMemoryBlobStore, InMemoryStore

ADA = Principal(subject="ada", issuer="i")
BOB = Principal(subject="bob", issuer="i")


class Clock:
    def __init__(self, now: float = 1_000_040.0) -> None:  # 20 s into a minute
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_limits_need_a_count_and_a_window():
    with pytest.raises(ValueError):
        Limit(0, per=60)
    from datetime import timedelta

    assert Limit(5, per=timedelta(minutes=2)).window == 120


async def test_sliding_window():
    clock = Clock()
    limiter = SlidingWindowLimiter(InMemoryRateLimitStore(), clock=clock)
    limit = Limit(10, per=60)
    for i in range(10):
        decision = await limiter.check("k", limit)
        assert decision.allowed and decision.remaining == 9 - i
    refused = await limiter.check("k", limit)
    assert not refused.allowed and refused.retry_after == 40  # the window ends in 40 s
    clock.now += 40 + 30  # next window, halfway: the previous one weighs 50 %
    # 11 counted before (10 + the refused one) * 0.5 = 5.5 used: 4 more allowed
    allowed = [(await limiter.check("k", limit)).allowed for _ in range(5)]
    assert allowed == [True, True, True, True, False]
    assert (await limiter.check("other", limit)).allowed  # keys are separate
    clock.now += 120
    assert (await limiter.check("k", limit)).remaining == 9  # all forgotten


async def test_looking_does_not_count():
    limiter = SlidingWindowLimiter(
        InMemoryRateLimitStore(), failed_auth=Limit(2, per=60), clock=Clock()
    )
    for _ in range(5):
        await limiter.before_auth("10.0.0.1")  # no failures yet: never refused
    await limiter.failed("10.0.0.1")
    await limiter.before_auth("10.0.0.1")
    await limiter.failed("10.0.0.1")
    with pytest.raises(RateLimited) as caught:
        await limiter.before_auth("10.0.0.1")
    assert caught.value.details["scope"] == "failed authentications"
    await limiter.before_auth("10.0.0.2")  # another address


async def test_actions_are_limited_in_the_manager():
    limiter = SlidingWindowLimiter(
        InMemoryRateLimitStore(),
        actions={"invoices:create": Limit(2, per=60), "invoices:*": Limit(3, per=60)},
        exempt=lambda p: p.subject == "bob",
        clock=Clock(),
    )
    store = InMemoryStore.from_registry(registry)

    class Silent:
        async def send(self, message: str) -> None:
            pass

    manager = ResourceManager(
        registry,
        store.unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": Silent()},
        rate_limiter=limiter,
    )
    data = {"customer": "ACME", "amount": "1"}
    first = await manager.create("invoices", data, actor=ADA)
    await manager.create("invoices", data, actor=ADA)
    with pytest.raises(RateLimited) as caught:
        await manager.create("invoices", data, actor=ADA)
    assert caught.value.code == "RE-E710" and caught.value.details["scope"] == "invoices:create"
    await manager.get("invoices", first.id, actor=ADA)  # the third invoices:* request
    with pytest.raises(RateLimited):
        await manager.get("invoices", first.id, actor=ADA)  # invoices:* reached
    for _ in range(5):
        await manager.create("invoices", data, actor=BOB)  # exempt
        await manager.create("invoices", data, actor=system_principal("job"))  # engine jobs
    await manager.get("invoices", first.id, actor=BOB)
