"""AWS adapters for Resource Engine: SQS/SNS publishers and the SQS consumer source.

Install the extras for the services you use: ``resource-engine-aws[sqs,sns]``.
"""

from resource_engine_aws.sns import SnsMessagePublisher
from resource_engine_aws.sqs import SqsMessagePublisher
from resource_engine_aws.sqs_source import SqsMessageSource, decode_sqs_body

__all__ = [
    "SnsMessagePublisher",
    "SqsMessagePublisher",
    "SqsMessageSource",
    "decode_sqs_body",
]
