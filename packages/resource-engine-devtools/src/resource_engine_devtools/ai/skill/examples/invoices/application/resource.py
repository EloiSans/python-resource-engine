from __future__ import annotations

from resource_engine import DocumentSlot, Resource

from invoices.application import hooks
from invoices.application.actions import RecalculateTotals, RenderSummary
from invoices.application.schemas import InvoiceCreate, InvoiceRead, InvoiceUpdate, RejectInvoice
from invoices.domain.entities import Invoice
from invoices.domain.state_machine import InvoiceStateMachine


class InvoiceResource(Resource):
    """Gestió de factures de l'organització.

    Permet crear, consultar i modificar factures, així com gestionar-ne el
    procés d'aprovació i cobrament.

    Business area:
        Facturació

    Actors:
        - Administrador
        - Comptable
        - Responsable
    """

    model = Invoice
    schemas = {"create": InvoiceCreate, "update": InvoiceUpdate, "read": InvoiceRead}
    operations = ["list", "get", "create", "update", "delete"]
    operation_docs = {
        "list": "Llista les factures, filtrables per estat i client.",
        "get": "Consulta una factura.",
        "create": "Crea una factura en estat esborrany.",
        "update": "Modifica una factura mentre és un esborrany.",
        "delete": "Elimina una factura en esborrany.",
    }
    state_machine = InvoiceStateMachine
    transition_schemas = {"reject": RejectInvoice}
    actions = [RecalculateTotals, RenderSummary]
    documents = [
        DocumentSlot(
            "signed_pdf",
            content_types=["application/pdf"],
            max_bytes=10 * 1024 * 1024,
            required_for=["approve"],
            description="Factura signada pel client; cal per aprovar-la.",
        ),
        DocumentSlot(
            "attachments",
            multiple=True,
            max_bytes=25 * 1024 * 1024,
            description="Justificants i altra documentació de suport.",
        ),
        DocumentSlot(
            "summary",
            content_types=["text/markdown"],
            upload="api",
            description="Resum generat per l'acció render_summary.",
        ),
    ]
    hooks = {
        "before_create": [hooks.validate_amount],
        "before_update": [hooks.ensure_editable],
        "after_update": [hooks.validate_amount],
        "before_delete": [hooks.ensure_editable],
        "after_commit": [hooks.notify_approval],
    }
    filterable = ["status", "customer"]
    sortable = ["id", "amount", "issue_date"]
    # internal notes: only roles granted invoices:read:notes / invoices:write:notes
    restricted_fields = ["notes"]
    # optimistic concurrency: ETag / If-Match, stale writes are refused (409)
    version_field = "version"
