"""S3-compatible ``BlobStore`` for Resource Engine documents."""

from resource_engine_s3.store import S3BlobStore, s3_client_options

__all__ = ["S3BlobStore", "s3_client_options"]
