"""Pull consumer loop over any ``MessageSource``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from dataclasses import dataclass

from resource_engine.messaging.processing import MessageProcessor, Outcome, ProcessResult
from resource_engine.ports.messaging import MessageSource, ReceivedMessage, SupportsDeadLetter

log = logging.getLogger("resource_engine.messaging")


@dataclass
class ConsumerStats:
    received: int = 0
    acked: int = 0
    duplicates: int = 0
    retried: int = 0
    dead_lettered: int = 0

    def add(self, other: ConsumerStats) -> None:
        for name in vars(self):
            setattr(self, name, getattr(self, name) + getattr(other, name))


class MessageConsumer:
    """Receives batches from a source and settles each message after processing.

    Messages in a batch are processed in order. When a message fails, later
    messages with the same ``key`` in that batch are retried without being
    processed, so per-entity order holds on FIFO queues and ordered subscriptions.
    With ``concurrency`` above 1, messages with different keys run concurrently
    (up to that many at once) while each key keeps its order; messages without a
    key are independent.

    Dead-lettering uses the source's ``dead_letter`` when it has one; otherwise
    the message is retried with the maximum delay and the broker's redrive policy
    is expected to move it.

    Args:
        source: Where messages come from.
        processor: What to do with each message.
        batch_size: Messages per ``receive`` call.
        wait_seconds: Long-poll wait when the source is empty.
        concurrency: Keys processed at the same time within a batch.
    """

    def __init__(
        self,
        source: MessageSource,
        processor: MessageProcessor,
        *,
        batch_size: int = 10,
        wait_seconds: float = 20.0,
        concurrency: int = 1,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self.source = source
        self.processor = processor
        self.batch_size = batch_size
        self.wait_seconds = wait_seconds
        self.concurrency = concurrency

    async def run_once(self) -> ConsumerStats:
        """Receive and settle one batch."""
        messages = await self.source.receive(self.batch_size, self.wait_seconds)
        stats = ConsumerStats(received=len(messages))
        if self.concurrency == 1:
            outcomes = await self._in_order(messages)
        else:
            outcomes = await self._by_key(messages)
        await self._settle(list(zip(messages, outcomes, strict=True)), stats)
        return stats

    async def _in_order(self, messages: list[ReceivedMessage]) -> list[ProcessResult]:
        """One by one; a failed key blocks its later messages."""
        results: list[ProcessResult] = []
        blocked: set[str] = set()
        for message in messages:
            key = message.envelope.key if message.envelope else None
            if key is not None and key in blocked:
                result = ProcessResult(
                    Outcome.RETRY, "an earlier message with this key failed", blocked=True
                )
                await self.processor.observe(message, result, 0.0)
            else:
                result = await self.processor.process(message)
                if result.outcome is Outcome.RETRY and key is not None:
                    blocked.add(key)
            results.append(result)
        return results

    async def _by_key(self, messages: list[ReceivedMessage]) -> list[ProcessResult]:
        """Groups by key run concurrently; each group in order."""
        groups: dict[object, list[int]] = {}
        for index, message in enumerate(messages):
            key = message.envelope.key if message.envelope else None
            groups.setdefault(key if key is not None else object(), []).append(index)
        results: list[ProcessResult | None] = [None] * len(messages)
        limit = asyncio.Semaphore(self.concurrency)

        async def run(indexes: list[int]) -> None:
            async with limit:
                outcomes = await self._in_order([messages[i] for i in indexes])
            for i, result in zip(indexes, outcomes, strict=True):
                results[i] = result

        await asyncio.gather(*(run(indexes) for indexes in groups.values()))
        return [r for r in results if r is not None]

    async def _settle(
        self, results: list[tuple[ReceivedMessage, ProcessResult]], stats: ConsumerStats
    ) -> None:
        settled = [m for m, r in results if r.outcome.settled]
        stats.acked = sum(r.outcome is Outcome.ACK for _, r in results)
        stats.duplicates = sum(r.outcome is Outcome.DUPLICATE for _, r in results)
        if settled:
            await self.source.ack(settled)

        retries: dict[float, list[ReceivedMessage]] = defaultdict(list)
        for message, result in results:
            if result.outcome is Outcome.RETRY:
                retries[result.delay_seconds].append(message)
                stats.retried += 1
        for delay, batch in retries.items():
            await self.source.retry(batch, delay)

        dead = [(m, r) for m, r in results if r.outcome is Outcome.DEAD_LETTER]
        stats.dead_lettered = len(dead)
        for message, result in dead:
            if isinstance(self.source, SupportsDeadLetter):
                await self.source.dead_letter([message], result.error or "rejected")
            else:
                log.error(
                    "Source %s cannot dead-letter; leaving message %s to the broker's "
                    "redrive policy",
                    type(self.source).__name__,
                    message.message_id,
                )
                await self.source.retry([message], self.processor.retry.max_delay)

    async def run_forever(self, stop: asyncio.Event | None = None) -> ConsumerStats:
        """Loop until ``stop`` is set. Source errors are logged and retried with backoff."""
        stop = stop or asyncio.Event()
        total = ConsumerStats()
        backoff = 1.0
        while not stop.is_set():
            try:
                total.add(await self.run_once())
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Consumer %s failed; retrying in %.0fs", self.processor.name, backoff)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                backoff = min(backoff * 2, 60.0)
        return total
