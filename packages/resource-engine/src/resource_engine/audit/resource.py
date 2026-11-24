"""The built-in, read-only ``audit_log`` resource."""

from __future__ import annotations

from resource_engine.application.resource import Resource
from resource_engine.audit.model import AuditEntry, AuditEntryRead, AuditSeal, AuditSealRead


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
    filterable = [
        "resource",
        "entity_id",
        "tenant",
        "actor",
        "outcome",
        "operation",
        "correlation_id",
        "seal",
    ]
    sortable = ["at"]
    max_page_size = 500
    authz_wildcards = False
    audit = False


class AuditSealResource(Resource):
    """Seals of the audit trail: one per sealed batch of entries, chained.

    Written by ``AuditSealer`` only. Sensitive like the audit log.

    Business area:
        Audit
    """

    name = "audit_seals"
    model = AuditSeal
    id_field = "sequence"
    id_type = int
    schemas = {"read": AuditSealRead}
    operations = ["list", "get"]
    operation_docs = {
        "list": "Lists the seals of the audit trail, in chain order.",
        "get": "Returns a seal.",
    }
    sortable = ["sequence"]
    max_page_size = 500
    authz_wildcards = False
    audit = False
