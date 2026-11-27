"""AWS adapters for Resource Engine: SQS/SNS publishers, the SQS consumer source
and the SES email sender.

Install the extras for the services you use: ``resource-engine-aws[sqs,sns,ses]``.
"""

from resource_engine_aws.ses import SesEmailSender
from resource_engine_aws.sns import SnsMessagePublisher
from resource_engine_aws.sqs import SqsMessagePublisher
from resource_engine_aws.sqs_source import SqsMessageSource, decode_sqs_body

__all__ = [
    "SesEmailSender",
    "SnsMessagePublisher",
    "SqsMessagePublisher",
    "SqsMessageSource",
    "decode_sqs_body",
]
