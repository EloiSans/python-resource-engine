"""Documents attached to resources: slots, uploads, processing and clean-up.

See docs/documents.md.
"""

from resource_engine.application.slots import DocumentSlot
from resource_engine.documents.events import (
    DocumentAttached,
    DocumentDetached,
    DocumentEvent,
    DocumentProcessed,
    DocumentRejected,
)
from resource_engine.documents.janitor import DocumentJanitor, JanitorStats
from resource_engine.documents.machine import DocumentStateMachine
from resource_engine.documents.manager import (
    ContextDocuments,
    DocumentManager,
    UploadTicket,
)
from resource_engine.documents.model import (
    Document,
    DocumentRead,
    NewDocument,
    content_disposition,
    safe_filename,
)
from resource_engine.documents.pipeline import (
    DocumentPipeline,
    DocumentProcessor,
    ProcessingResult,
)
from resource_engine.documents.resource import DocumentResource

__all__ = [
    "ContextDocuments",
    "Document",
    "DocumentAttached",
    "DocumentDetached",
    "DocumentEvent",
    "DocumentJanitor",
    "DocumentManager",
    "DocumentPipeline",
    "DocumentProcessed",
    "DocumentProcessor",
    "DocumentRead",
    "DocumentRejected",
    "DocumentResource",
    "DocumentSlot",
    "DocumentStateMachine",
    "JanitorStats",
    "NewDocument",
    "ProcessingResult",
    "UploadTicket",
    "content_disposition",
    "safe_filename",
]
