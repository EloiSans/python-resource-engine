from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

#: SQS SendMessageBatch and SNS PublishBatch limits.
MAX_ENTRIES = 10
MAX_BATCH_BYTES = 256 * 1024


def string_attributes(attrs: dict[str, str]) -> dict[str, dict[str, str]]:
    return {k: {"DataType": "String", "StringValue": v} for k, v in attrs.items()}


def entry_size(entry: dict[str, Any], body_key: str) -> int:
    size = len(entry[body_key].encode())
    for name, attr in entry.get("MessageAttributes", {}).items():
        size += len(name.encode()) + len(attr["DataType"]) + len(attr["StringValue"].encode())
    return size


def chunk(entries: Sequence[dict[str, Any]], body_key: str) -> Iterator[list[dict[str, Any]]]:
    """Split entries into batches that respect the count and total-size limits."""
    batch: list[dict[str, Any]] = []
    size = 0
    for entry in entries:
        n = entry_size(entry, body_key)
        if n > MAX_BATCH_BYTES:
            raise ValueError(f"Message {entry['Id']} is {n} bytes; the limit is {MAX_BATCH_BYTES}")
        if batch and (len(batch) == MAX_ENTRIES or size + n > MAX_BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(entry)
        size += n
    if batch:
        yield batch
