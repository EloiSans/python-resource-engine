# resource-engine-rabbitmq

RabbitMQ adapters for Resource Engine, on aio-pika:

- `RabbitMqMessagePublisher`: persistent messages with publisher confirms (at-least-once).
- `RabbitMqMessageSource`: consumer source for quorum queues, with dead-lettering.
- `declare_topology`: topic exchange, quorum queue, dead-letter exchange and queue.

Tested against `rabbitmq:4-management` (see `compose.emulators.yaml`; UI on :15672).
See the [adapter catalogue](../../docs/adapters.md). Part of [Resource Engine](../../README.md).
