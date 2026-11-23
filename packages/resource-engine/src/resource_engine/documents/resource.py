from __future__ import annotations

from resource_engine.application.resource import Resource
from resource_engine.documents.machine import DocumentStateMachine
from resource_engine.documents.model import Document, DocumentRead


class DocumentResource(Resource):
    """Files attached to entities of other resources.

    Business resources declare slots (``documents = [DocumentSlot(...)]``); the
    engine stores each file's content in the blob store and its metadata here.
    Documents are created, replaced and removed through the owner resource
    (``attach`` / ``detach``), never directly.

    Business area:
        Documents
    """

    name = "documents"
    model = Document
    id_type = str
    schemas = {"read": DocumentRead}
    operations = ["list", "get"]
    operation_docs = {
        "list": "Lists documents, filterable by owner, slot and status.",
        "get": "Returns a document's metadata.",
    }
    state_machine = DocumentStateMachine
    expose_transitions = False
    authz_wildcards = False  # raw document queries: use the owner's routes instead
    filterable = ["owner_resource", "owner_id", "slot", "status"]
    sortable = ["created_at", "filename", "size"]
