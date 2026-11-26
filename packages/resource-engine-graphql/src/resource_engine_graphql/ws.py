"""GraphQL over WebSocket (the ``graphql-transport-ws`` protocol), for
subscriptions; queries and mutations work on it too.

https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md

- The client opens the socket with subprotocol ``graphql-transport-ws`` and
  sends ``connection_init`` within ``init_timeout`` seconds; with an
  ``authenticator`` its payload must carry ``{"authorization": "Bearer <token>"}``
  (browsers cannot set headers on WebSockets).
- ``subscribe`` (id, payload) starts an operation; the server answers ``next``
  per result and ``complete`` at the end, or ``error`` when it does not start.
- ``complete`` from the client stops one; ``ping``/``pong`` keep it alive.

Close codes: 4400 bad message, 4401 not initialised or not authenticated,
4403 forbidden, 4408 init timeout, 4409 duplicate id, 4429 too many inits.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from resource_engine.errors import AuthenticationFailed

from resource_engine_graphql.execution import GraphQLAdapter

logger = logging.getLogger("resource_engine.graphql")

PROTOCOL = "graphql-transport-ws"


async def serve_websocket(
    websocket: Any,
    adapter: GraphQLAdapter,
    *,
    authenticator: Any = None,
    required: bool = True,
    init_timeout: float = 10.0,
    max_operations: int = 50,
) -> None:
    """Run one ``graphql-transport-ws`` connection (a Starlette ``WebSocket``)."""
    from starlette.websockets import WebSocketDisconnect

    if PROTOCOL not in websocket.scope.get("subprotocols", []):
        await websocket.close(code=4406)
        return
    await websocket.accept(subprotocol=PROTOCOL)
    tasks: dict[str, asyncio.Task[None]] = {}
    sending = asyncio.Lock()

    async def send(message: dict[str, Any]) -> None:
        async with sending:
            await websocket.send_text(json.dumps(message, default=str))

    async def close(code: int, reason: str) -> None:
        with contextlib.suppress(Exception):
            await websocket.close(code=code, reason=reason)

    try:
        try:
            first = await asyncio.wait_for(_receive(websocket), init_timeout)
        except TimeoutError:
            await close(4408, "Connection initialisation timeout")
            return
        if first is None or first.get("type") != "connection_init":
            await close(4401, "Unauthorized")
            return
        try:
            actor = await _authenticate(first.get("payload"), authenticator, required)
        except AuthenticationFailed as exc:
            await close(4403, exc.message[:120])
            return
        await send({"type": "connection_ack"})

        async def run(op_id: str, payload: dict[str, Any]) -> None:
            try:
                async for result in adapter.subscribe(
                    payload.get("query"),
                    payload.get("variables"),
                    payload.get("operationName"),
                    actor=actor,
                    extensions=payload.get("extensions"),
                ):
                    if result.get("data") is None and result.get("errors"):
                        await send({"id": op_id, "type": "error", "payload": result["errors"]})
                        return
                    await send({"id": op_id, "type": "next", "payload": result})
                await send({"id": op_id, "type": "complete"})
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GraphQL WebSocket operation failed")
                with contextlib.suppress(Exception):
                    await send(
                        {"id": op_id, "type": "error", "payload": [{"message": "Internal error"}]}
                    )
            finally:
                tasks.pop(op_id, None)

        while True:
            message = await _receive(websocket)
            if message is None:
                await close(4400, "Invalid message")
                return
            kind = message.get("type")
            if kind == "ping":
                await send({"type": "pong"})
            elif kind == "pong":
                continue
            elif kind == "connection_init":
                await close(4429, "Too many initialisation requests")
                return
            elif kind == "subscribe":
                op_id, payload = message.get("id"), message.get("payload")
                if not isinstance(op_id, str) or not isinstance(payload, dict):
                    await close(4400, "Invalid subscribe message")
                    return
                if op_id in tasks:
                    await close(4409, f"Subscriber for {op_id} already exists")
                    return
                if len(tasks) >= max_operations:
                    await send(
                        {
                            "id": op_id,
                            "type": "error",
                            "payload": [{"message": "Too many operations on this connection"}],
                        }
                    )
                    continue
                tasks[op_id] = asyncio.create_task(run(op_id, payload))
            elif kind == "complete":
                task = tasks.pop(str(message.get("id")), None)
                if task is not None:
                    task.cancel()
            else:
                await close(4400, f"Unknown message type {kind!r}")
                return
    except WebSocketDisconnect:
        pass
    finally:
        for task in list(tasks.values()):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)


async def _receive(websocket: Any) -> dict[str, Any] | None:
    text = await websocket.receive_text()
    try:
        message = json.loads(text)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


async def _authenticate(payload: Any, authenticator: Any, required: bool) -> Any:
    if authenticator is None:
        return None
    payload = payload if isinstance(payload, dict) else {}
    header = str(payload.get("authorization") or payload.get("Authorization") or "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        if required:
            raise AuthenticationFailed(
                "Missing bearer token",
                reason="missing",
                hint='Send {"authorization": "Bearer <token>"} in connection_init.',
            )
        return None
    return await authenticator.authenticate(token.strip())


__all__ = ["PROTOCOL", "serve_websocket"]
