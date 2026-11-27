"""AWS adapters for Resource Engine: SQS/SNS publishers, the SQS consumer source,
the SES email sender, EventBridge, Athena and DynamoDB repositories.

Install the extras for the services you use: ``resource-engine-aws[sqs,sns,ses]``.
"""

from resource_engine_aws.ses import SesEmailSender, parse_ses
from resource_engine_aws.sns import SnsMessagePublisher
from resource_engine_aws.sqs import SqsMessagePublisher
from resource_engine_aws.sqs_source import SqsMessageSource, decode_sqs_body

__all__ = [
    "AthenaEngine",
    "decode_sqs_body",
    "DynamoDbStore",
    "DynamoTable",
    "EventBridgePublisher",
    "parse_ses",
    "SesEmailSender",
    "SnsMessagePublisher",
    "SqsMessagePublisher",
    "SqsMessageSource",
]

#: Imported on first use, so each needs only its own extra.
_LAZY = {
    "AthenaEngine": "resource_engine_aws.athena",
    "DynamoDbStore": "resource_engine_aws.dynamodb",
    "DynamoTable": "resource_engine_aws.dynamodb",
    "EventBridgePublisher": "resource_engine_aws.eventbridge",
}


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name in _LAZY:
        import importlib

        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(name)
