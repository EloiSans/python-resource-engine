# resource-engine-clamav

ClamAV antivirus scanning for Resource Engine documents: `ClamAvScanner` is a
document processor that streams each upload to `clamd` (`INSTREAM`, TCP or a
Unix socket) and rejects infected files. No dependencies.

See [document processors](../../docs/documents.md#reference-processors).
Part of [Resource Engine](../../README.md).
