from __future__ import annotations

from datetime import timedelta

from resource_engine import DocumentSlot, Machine, Reference, Resource, Timer

from invoices.application import hooks
from invoices.application.actions import RecalculateTotals, RenderSummary
from invoices.application.schemas import InvoiceCreate, InvoiceRead, InvoiceUpdate, RejectInvoice
from invoices.domain.entities import Invoice
from invoices.domain.events import InvoiceTotalsRecalculated
from invoices.domain.state_machine import InvoiceDelivery, InvoiceStateMachine


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
    # two independent state machines: approval (status) and delivery to the
    # customer (delivery_status); since_field: when it entered its current
    # approval state, stamped by the engine
    machines = {
        "approval": Machine(InvoiceStateMachine, field="status", since_field="status_changed_at"),
        "delivery": Machine(InvoiceDelivery, field="delivery_status"),
    }
    timers = [
        Timer(
            "reject",
            after=timedelta(days=30),
            data={"reason": "Sense resposta en 30 dies"},
            description="Les factures pendents d'aprovar més de 30 dies es rebutgen soles.",
        )
    ]
    transition_schemas = {"reject": RejectInvoice}
    actions = [RecalculateTotals, RenderSummary]
    events = [InvoiceTotalsRecalculated]
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
        "after_transition:approve": [hooks.request_approval_email],
        "after_commit": [hooks.notify_approval],
    }
    # the registered customer; deleting a customer with invoices is refused (409)
    references = {
        "customer_id": Reference(
            "customers",
            name="customer_record",
            description="Client registrat al qual s'emet la factura.",
        )
    }
    filterable = ["status", "delivery_status", "customer", "customer_id"]
    sortable = ["id", "amount", "issue_date"]
    # internal notes: only roles granted invoices:read:notes / invoices:write:notes
    restricted_fields = ["notes"]
    # optimistic concurrency: ETag / If-Match, stale writes are refused (409)
    version_field = "version"
