"""``ActivityLog`` in this process (tests, single-process apps)."""

from __future__ import annotations

from datetime import datetime

from resource_engine.ports.activity import DeliveryRecord


class InMemoryActivityLog:
    def __init__(self) -> None:
        self.delivered: list[DeliveryRecord] = []

    async def record_delivery(self, record: DeliveryRecord) -> None:
        self.delivered.append(record)

    async def deliveries(self, message_id: str) -> list[DeliveryRecord]:
        return sorted((r for r in self.delivered if r.message_id == message_id), key=lambda r: r.at)

    async def purge(self, before: datetime, limit: int) -> int:
        old = [r for r in self.delivered if r.at < before][:limit]
        for record in old:
            self.delivered.remove(record)
        return len(old)


__all__ = ["InMemoryActivityLog"]
