"""AsyncAPI 3.0 document for the events an application publishes and consumes.

```python
from resource_engine.messaging.asyncapi import asyncapi_document

document = asyncapi_document(
    registry,
    title="Invoices",
    version="1.0.0",
    consumers=[payments_router],
    servers={"production": {"host": "sns.eu-west-1.amazonaws.com", "protocol": "sns"}},
    channels={"invoices": {"address": "arn:aws:sns:eu-west-1:123:invoices"}},
)
```

- **Published (``send``):** one channel per resource. Its messages are the
  engine events the resource can emit (created, updated, deleted, transition,
  action, document events), with fields typed for the resource (id type,
  transition and state names), plus its declared ``Resource.events``.
- **Consumed (``receive``):** one channel per ``EventRouter``, with a message
  per registered event type. Its payload schema is the handler's ``payload=``
  type, or any object for untyped handlers.

Messages are the envelope JSON (``format="envelope"``, the default) or
structured CloudEvents 1.0 (``format="cloudevents"``), per document or per
channel. See docs/asyncapi.md.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import TypeAdapter

from resource_engine.application.events import (
    ActionExecuted,
    ResourceCreated,
    ResourceDeleted,
    ResourceUpdated,
    TransitionExecuted,
)
from resource_engine.application.registry import ResourceRegistry
from resource_engine.application.resource import ResourceDefinition
from resource_engine.domain.events import event_doc
from resource_engine.errors import ConfigurationError
from resource_engine.messaging.cloudevents import CONTENT_TYPE as CLOUDEVENTS_CONTENT_TYPE
from resource_engine.messaging.consumer import EventRouter
from resource_engine.messaging.envelope import SCHEMA_VERSION
from resource_engine.versions import event_version

ASYNCAPI_VERSION = "3.0.0"

type Format = Literal["envelope", "cloudevents"]

_KEY = re.compile(r"[^A-Za-z0-9.\-_]")
_REF = "#/components/schemas/{model}"
_OPTIONAL_STRING = {"type": ["string", "null"]}


def _key(*parts: str) -> str:
    return ".".join(_KEY.sub("_", p) for p in parts)


def _summary(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    return inspect.cleandoc(text).split("\n\n")[0].replace("\n", " ").strip()


class _Builder:
    def __init__(self, format: Format, source: str | None, type_prefix: str) -> None:
        self.format = format
        self.source = source
        self.type_prefix = type_prefix
        self.schemas: dict[str, Any] = {}
        self.messages: dict[str, Any] = {}

    # ------------------------------------------------------------- schemas

    def type_schema(self, annotation: Any) -> dict[str, Any]:
        schema = TypeAdapter(annotation).json_schema(mode="serialization", ref_template=_REF)
        self.schemas.update(schema.pop("$defs", {}))
        return schema

    def event_schema(self, event_cls: type) -> dict[str, Any]:
        """A domain event dataclass's payload (its fields but ``occurred_at``)."""
        schema = self.type_schema(event_cls)
        schema.pop("title", None)
        if dataclasses.is_dataclass(event_cls) and isinstance(event_cls, type):
            doc = event_doc(event_cls)
            if doc:
                schema["description"] = inspect.cleandoc(doc)
            else:
                schema.pop("description", None)
        schema.get("properties", {}).pop("occurred_at", None)
        if "required" in schema:
            schema["required"] = [f for f in schema["required"] if f != "occurred_at"]
            if not schema["required"]:
                del schema["required"]
        return schema

    def wire(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        resource: str | None,
        format: Format,
        source: str | None,
        type_prefix: str,
        schema_version: str = SCHEMA_VERSION,
    ) -> dict[str, Any]:
        if format == "cloudevents":
            properties: dict[str, Any] = {
                "specversion": {"const": "1.0"},
                "id": {"type": "string"},
                "source": {"const": source} if source else {"type": "string"},
                "type": {"const": f"{type_prefix}{event_type}"},
                "time": {"type": "string", "format": "date-time"},
                "datacontenttype": {"const": "application/json"},
                "schemaversion": {"const": schema_version},
                "subject": {"type": "string", "description": "Ordering key"},
                "partitionkey": {"type": "string", "description": "Ordering key"},
                "correlationid": {"type": "string"},
                "resource": {"const": resource} if resource else {"type": "string"},
                "traceparent": {"type": "string"},
                "tracestate": {"type": "string"},
                "data": payload,
            }
            required = ["specversion", "id", "source", "type", "time", "data"]
        else:
            properties = {
                "id": {"type": "string", "description": "Unique message id"},
                "type": {"const": event_type},
                "occurred_at": {"type": "string", "format": "date-time"},
                "correlation_id": {
                    **_OPTIONAL_STRING,
                    "description": "Id of the operation that produced the event",
                },
                "resource": {"const": resource} if resource else _OPTIONAL_STRING,
                "key": {
                    **_OPTIONAL_STRING,
                    "description": "Ordering key: <resource>:<entity_id>",
                },
                "schema_version": {"const": schema_version},
                "payload": payload,
                "trace": {
                    "type": "object",
                    "description": "W3C trace context of the producing operation",
                    "properties": {
                        "traceparent": {"type": "string"},
                        "tracestate": {"type": "string"},
                    },
                },
            }
            required = ["id", "type", "occurred_at", "schema_version", "payload"]
        return {"type": "object", "properties": properties, "required": required}

    def message(
        self,
        key: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        resource: str | None,
        summary: str | None,
        description: str | None = None,
        channel: Mapping[str, Any],
        schema_version: str = SCHEMA_VERSION,
        deprecation: Any = None,
    ) -> dict[str, Any]:
        format = channel.get("format", self.format)
        if format not in ("envelope", "cloudevents"):
            raise ConfigurationError(
                f"AsyncAPI channel format must be envelope or cloudevents, not {format!r}",
                doc="docs/asyncapi.md#servers-and-channels",
            )
        self.schemas[key] = self.wire(
            event_type,
            payload,
            resource=resource,
            format=format,
            source=channel.get("source", self.source),
            type_prefix=channel.get("type_prefix", self.type_prefix),
            schema_version=schema_version,
        )
        versioned = schema_version != SCHEMA_VERSION
        message: dict[str, Any] = {
            "name": event_type,
            "title": f"{event_type} v{schema_version}" if versioned else event_type,
            "contentType": "application/json" if format == "envelope" else CLOUDEVENTS_CONTENT_TYPE,
            "payload": {"$ref": _REF.format(model=key)},
            "correlationId": {
                "location": "$message.payload#/correlation_id"
                if format == "envelope"
                else "$message.payload#/correlationid"
            },
        }
        if format == "envelope":
            message["headers"] = {
                "type": "object",
                "description": "Broker message attributes (SNS/SQS attributes, "
                "Service Bus application properties, Pub/Sub attributes, AMQP headers)",
                "properties": {
                    "event_type": {"const": event_type},
                    "event_id": {"type": "string"},
                    "schema_version": {"const": schema_version},
                    "resource": {"type": "string"},
                    "correlation_id": {"type": "string"},
                    "traceparent": {"type": "string"},
                },
            }
        if summary:
            message["summary"] = summary
        if description and description != summary:
            message["description"] = description
        if deprecation is not None:  # AsyncAPI has no deprecated messages: schema + extension
            self.schemas[key]["deprecated"] = True
            message["x-deprecation"] = deprecation.to_dict()
            notice = deprecation.notice()
            message["description"] = (
                f"{notice}\n\n{message['description']}" if message.get("description") else notice
            )
        self.messages[key] = message
        return {"$ref": f"#/components/messages/{key}"}

    # ----------------------------------------------------- resource events

    def resource_events(
        self, d: ResourceDefinition, channel: Mapping[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Message references by event name, for one resource's channel."""
        refs: dict[str, dict[str, Any]] = {}
        entity_id = self.type_schema(d.id_type)

        def base(**fields: Any) -> dict[str, Any]:
            properties = {"resource": {"const": d.name}, "entity_id": entity_id, **fields}
            return {
                "type": "object",
                "properties": properties,
                "required": list(properties),
            }

        def add(
            event: type | str,
            payload: dict[str, Any],
            summary: str,
            *,
            schema_version: str = SCHEMA_VERSION,
            deprecation: Any = None,
        ) -> None:
            name = event if isinstance(event, str) else event.__name__
            versioned = schema_version != SCHEMA_VERSION
            ref = f"{name}.v{schema_version}" if versioned else name
            refs[ref] = self.message(
                _key(d.name, name, f"v{schema_version}") if versioned else _key(d.name, name),
                name,
                payload,
                resource=d.name,
                summary=summary,
                channel=channel,
                schema_version=schema_version,
                deprecation=deprecation,
            )

        if d.enabled("create"):
            add(ResourceCreated, base(), f"{d.name}: a record was created.")
        if d.enabled("update"):
            add(
                ResourceUpdated,
                base(
                    changes={
                        "type": "object",
                        "description": "Changed fields: [old value, new value]",
                        "additionalProperties": {"type": "array", "minItems": 2, "maxItems": 2},
                    }
                ),
                f"{d.name}: fields of a record changed.",
            )
        if d.enabled("delete"):
            add(ResourceDeleted, base(), f"{d.name}: a record was deleted.")
        if d.machine is not None and not _internal_machine(d):
            states = list(
                dict.fromkeys(
                    s.value if s.value is not None else s.id
                    for m in d.machines.values()
                    for s in m.info.states
                )
            )
            add(
                TransitionExecuted,
                base(
                    transition={"enum": list(d.transitions)},
                    source={"enum": states},
                    target={"enum": states},
                    machine={"enum": list(d.machines)},
                ),
                f"{d.name}: a record changed state.",
            )
        if d.actions:
            add(
                ActionExecuted,
                base(action={"enum": list(d.actions)}),
                f"{d.name}: an action ran on a record.",
            )
        if d.document_slots:
            self._document_events(d, base, add)
        for event in d.events:
            doc = event_doc(event)
            if event.__name__ in refs:
                raise ConfigurationError(
                    f"{d.name}: the declared event {event.__name__} has the name of an "
                    "engine event",
                    hint="Rename the domain event.",
                )
            add(
                event,
                self.event_schema(event),
                _summary(doc) or f"{event.__name__}.",
                schema_version=str(event_version(event)),
                deprecation=getattr(event, "deprecation", None),
            )
            for number, older in sorted(getattr(event, "previous_versions", {}).items()):
                add(
                    event.__name__,
                    self.event_schema(older.schema),
                    f"{event.__name__}, version {number} (published for older consumers).",
                    schema_version=str(number),
                    deprecation=older.deprecation,
                )
        return refs

    def _document_events(self, d: ResourceDefinition, base: Any, add: Any) -> None:
        from resource_engine.documents.events import (
            DocumentAttached,
            DocumentDetached,
            DocumentProcessed,
            DocumentRejected,
        )

        slot = {"enum": list(d.document_slots)}
        common = {"document_id": {"type": "string"}, "slot": slot}
        add(
            DocumentAttached,
            base(
                **common,
                content_type={"type": "string"},
                size={"type": ["integer", "null"]},
                processing={"type": "boolean"},
            ),
            f"{d.name}: a document was attached to a record.",
        )
        add(DocumentDetached, base(**common), f"{d.name}: a document of a record was removed.")
        if any(s.processing for s in d.document_slots.values()):
            add(DocumentProcessed, base(**common), "A document finished processing.")
            add(
                DocumentRejected,
                base(**common, error={"type": "string"}),
                "A document was rejected while processing.",
            )


def _internal_machine(d: ResourceDefinition) -> bool:
    """The documents resource changes state through the document pipeline,
    which records document events on the owner instead of transitions."""
    from resource_engine.documents.resource import DocumentResource

    return issubclass(d.resource_cls, DocumentResource)


def _event_doc(router: EventRouter, event_type: str) -> str | None:
    payload = router.payload_type(event_type)
    for handler in router.handlers(event_type):
        doc = inspect.getdoc(handler)
        if doc:
            return doc
    if isinstance(payload, type) and dataclasses.is_dataclass(payload):
        return event_doc(payload)
    if isinstance(payload, type):
        return inspect.getdoc(payload)
    return None


def asyncapi_document(
    registry: ResourceRegistry,
    *,
    title: str = "Resource Engine application",
    version: str = "1.0.0",
    description: str | None = None,
    resources: Sequence[str] | None = None,
    consumers: Sequence[EventRouter] = (),
    servers: Mapping[str, Mapping[str, Any]] | None = None,
    channels: Mapping[str, Mapping[str, Any]] | None = None,
    format: Format = "envelope",
    source: str | None = None,
    type_prefix: str = "",
) -> dict[str, Any]:
    """The AsyncAPI 3.0 document, as a JSON-compatible dict.

    Args:
        registry: The application's resources (published events).
        resources: Only these resources (default: every resource that emits events).
        consumers: Routers whose event types the application receives; each is
            one channel named after ``EventRouter(name=...)``.
        servers: AsyncAPI server objects by name (``host``, ``protocol``...).
        channels: Per-channel overrides by channel name (a resource name, or a
            consumer name): ``address``, ``description``, ``servers`` (server
            names), ``format``, ``source``, ``type_prefix``, ``bindings``.
        format: ``envelope`` (the engine's JSON) or ``cloudevents`` (structured
            CloudEvents 1.0, as the CloudEvents publishers send).
        source / type_prefix: CloudEvents ``source`` and type prefix.
    """
    channels = channels or {}
    servers = dict(servers or {})
    for name, server in servers.items():
        if "host" not in server or "protocol" not in server:
            raise ConfigurationError(
                f"AsyncAPI server {name!r} needs a host and a protocol",
                hint='e.g. {"host": "rabbitmq.internal:5672", "protocol": "amqp"}',
                doc="docs/asyncapi.md#servers-and-channels",
            )
    builder = _Builder(format, source, type_prefix)
    doc_channels: dict[str, Any] = {}
    operations: dict[str, Any] = {}

    def channel_object(name: str, default_description: str | None) -> dict[str, Any]:
        options = channels.get(name, {})
        unknown = [s for s in options.get("servers", ()) if s not in servers]
        if unknown:
            raise ConfigurationError(
                f"AsyncAPI channel {name!r} names unknown servers {unknown}",
                doc="docs/asyncapi.md#servers-and-channels",
            )
        obj: dict[str, Any] = {"address": options.get("address", name)}
        text = options.get("description", default_description)
        if text:
            obj["description"] = text
        if options.get("servers"):
            obj["servers"] = [{"$ref": f"#/servers/{s}"} for s in options["servers"]]
        if options.get("bindings"):
            obj["bindings"] = copy.deepcopy(dict(options["bindings"]))
        return obj

    for definition in registry:
        if resources is not None and definition.name not in resources:
            continue
        name = _key(definition.name)
        options = channels.get(definition.name, {})
        messages = builder.resource_events(definition, options)
        if not messages:
            continue
        channel = channel_object(definition.name, _summary(definition.description))
        channel["title"] = f"{definition.name} events"
        channel["messages"] = messages
        doc_channels[name] = channel
        operations[_key("publish", definition.name)] = {
            "action": "send",
            "channel": {"$ref": f"#/channels/{name}"},
            "title": f"Publish {definition.name} events",
            "summary": "Sent after each committed change, directly or through the outbox.",
            "messages": [{"$ref": f"#/channels/{name}/messages/{m}"} for m in messages],
        }

    for index, router in enumerate(consumers):
        consumer = router.name or f"consumer_{index + 1}"
        name = _key(consumer)
        if name in doc_channels:
            raise ConfigurationError(
                f"AsyncAPI channel {name!r} is both a resource and a consumer",
                hint="Name the EventRouter differently (EventRouter(name=...)).",
                doc="docs/asyncapi.md#servers-and-channels",
            )
        options = channels.get(consumer, {})
        messages: dict[str, Any] = {}
        for event_type in router.event_types:
            payload_type = router.payload_type(event_type)
            payload = (
                builder.event_schema(payload_type)
                if payload_type is not None
                else {"type": "object", "description": "Untyped: the handler reads a dict"}
            )
            text = _event_doc(router, event_type)
            messages[_key(event_type)] = builder.message(
                _key(consumer, event_type),
                event_type,
                payload,
                resource=None,
                summary=_summary(text),
                description=text,
                channel=options,
                schema_version=router.schema_version(event_type) or SCHEMA_VERSION,
            )
        channel = channel_object(consumer, router.description)
        channel["title"] = f"{consumer} (consumed)"
        channel["messages"] = messages
        doc_channels[name] = channel
        operations[_key("receive", consumer)] = {
            "action": "receive",
            "channel": {"$ref": f"#/channels/{name}"},
            "title": f"Receive in {consumer}",
            "summary": "Handled by the consumer's EventRouter; envelope or CloudEvents bodies.",
            "messages": [{"$ref": f"#/channels/{name}/messages/{m}"} for m in messages],
        }

    info: dict[str, Any] = {"title": title, "version": version}
    if description:
        info["description"] = description
    document: dict[str, Any] = {
        "asyncapi": ASYNCAPI_VERSION,
        "info": info,
        "defaultContentType": "application/json",
    }
    if servers:
        document["servers"] = {k: copy.deepcopy(dict(v)) for k, v in servers.items()}
    document["channels"] = doc_channels
    document["operations"] = operations
    document["components"] = {
        "messages": builder.messages,
        "schemas": dict(sorted(builder.schemas.items())),
    }
    return document


__all__ = ["ASYNCAPI_VERSION", "asyncapi_document"]
