"""Amazon EventBridge as a ``MessagePublisher`` (extra ``[eventbridge]``).

```python
async with session.create_client("events", region_name="eu-west-1") as events:
    publisher = EventBridgePublisher(events, bus="invoices", source="com.example.invoices")
```

Each envelope is one event: ``DetailType`` is the event type, ``Detail`` the
envelope as JSON (so consumers get the same envelope from any broker), and
``Time`` when it happened. Rules route events by ``source`` and
``detail-type`` (``{"detail-type": ["InvoiceApproved"]}``) to SQS, Lambda,
other buses or API destinations. Batches of 10 (the API's limit); entries
EventBridge refuses are reported in ``PublishFailed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope

BATCH = 10


class EventBridgePublisher:
    """``MessagePublisher`` over ``PutEvents``.

    Args:
        client: An aiobotocore ``events`` client (owned by the caller).
        bus: The event bus name or ARN (``default`` for the account's bus).
        source: The events' ``source`` (``com.example.invoices``).
    """

    def __init__(self, client: Any, *, bus: str = "default", source: str) -> None:
        self.client = client
        self.bus = bus
        self.source = source

    def entry(self, envelope: EventEnvelope) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "Source": self.source,
            "DetailType": envelope.type,
            "Detail": envelope.to_json(),
            "EventBusName": self.bus,
            "Time": envelope.occurred_at,
        }
        if envelope.resource:
            entry["Resources"] = [f"{self.source}:{envelope.resource}"]
        return entry

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        failed: list[str] = []
        errors: list[str] = []
        for start in range(0, len(messages), BATCH):
            batch = list(messages[start : start + BATCH])
            try:
                response = await self.client.put_events(Entries=[self.entry(m) for m in batch])
            except Exception as exc:  # the SDK's error hierarchy
                failed += [m.id for m in batch]
                errors.append(f"{type(exc).__name__}: {exc}")
                continue
            for envelope, result in zip(batch, response.get("Entries", []), strict=False):
                if result.get("ErrorCode"):
                    failed.append(envelope.id)
                    errors.append(f"{result['ErrorCode']}: {result.get('ErrorMessage')}")
        if failed:
            raise PublishFailed(
                f"EventBridge rejected {len(failed)} of {len(messages)} events: "
                f"{'; '.join(errors[:3])}",
                failed_ids=failed,
            )


__all__ = ["EventBridgePublisher"]
