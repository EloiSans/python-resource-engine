"""The built-in, read-only ``audit_log`` resource."""

from __future__ import annotations

from resource_engine.application.resource import Resource
from resource_engine.audit.model import AuditEntry, AuditEntryRead


class AuditLogResource(Resource):
    """Audit trail: committed changes and refused attempts.

    Entries are written by the engine only (no create, update or delete). The
    resource is sensitive: wildcard grants do not reach it.

    Business area:
        Audit
    """

    name = "audit_log"
    model = AuditEntry
    id_type = str
    schemas = {"read": AuditEntryRead}
    operations = ["list", "get"]
    operation_docs = {
        "list": "Lists audit entries; filter by resource and entity_id for a record's history.",
        "get": "Returns an audit entry.",
    }
    filterable = ["resource", "entity_id", "actor", "outcome", "operation", "correlation_id"]
    sortable = ["at"]
    max_page_size = 500
    authz_wildcards = False
    audit = False
