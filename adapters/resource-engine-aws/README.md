# resource-engine-aws

Amazon SQS and SNS for Resource Engine: the SQS and SNS publishers and the SQS
consumer source (`SqsMessageSource`). Each service is an extra: `sqs`, `sns`,
or `all`. Tested against the official AWS Emulator.

For document storage on S3 (AWS S3, MinIO, Garage, R2), use
`resource-engine-s3`: S3 is a protocol many vendors implement, so it has its
own package ([naming and packaging](../../docs/adapters.md#naming-and-packaging)).

See the [adapter catalogue](../../docs/adapters.md). Part of [Resource Engine](../../README.md).
