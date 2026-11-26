from __future__ import annotations

from resource_engine import ListQuery, OperationContext, Reference, Resource
from resource_engine.domain import DomainError

from invoices.application.schemas import InvoiceTagCreate, InvoiceTagRead, TagCreate, TagRead
from invoices.domain.entities import InvoiceTag, Tag


class TagResource(Resource):
    """Etiquetes per classificar factures.

    Business area:
        Facturació

    Actors:
        - Emissor de factures
    """

    model = Tag
    schemas = {"create": TagCreate, "update": TagCreate, "read": TagRead}
    operations = ["list", "get", "create", "update", "delete"]
    operation_docs = {
        "list": "Llista les etiquetes.",
        "get": "Consulta una etiqueta.",
        "create": "Crea una etiqueta.",
        "update": "Canvia el nom o el color d'una etiqueta.",
        "delete": "Elimina una etiqueta i les seves assignacions.",
    }
    filterable = ["name"]
    sortable = ["name"]


async def ensure_not_tagged(ctx: OperationContext) -> None:
    """Refusa assignar a una factura una etiqueta que ja té.

    Exceptions:
        DomainError: la factura ja té l'etiqueta.
    """
    entity = ctx.entity
    page = await ctx.repository().list(
        ListQuery(filters={"invoice_id": entity.invoice_id, "tag_id": entity.tag_id}, limit=1)
    )
    if page.total:
        raise DomainError("The invoice already has this tag", code="INVOICE_TAG")


class InvoiceTagResource(Resource):
    """Etiquetes assignades a factures (molts a molts).

    Les factures tenen la relació ``tags`` i les etiquetes la relació
    ``invoices``; assignar i treure etiquetes és crear i eliminar aquests
    registres.

    Business area:
        Facturació

    Actors:
        - Emissor de factures
    """

    model = InvoiceTag
    schemas = {"create": InvoiceTagCreate, "read": InvoiceTagRead}
    operations = ["list", "get", "create", "delete"]
    operation_docs = {
        "list": "Llista les assignacions d'etiquetes.",
        "get": "Consulta una assignació.",
        "create": "Assigna una etiqueta a una factura.",
        "delete": "Treu una etiqueta d'una factura.",
    }
    references = {
        "invoice_id": Reference("invoices", on_delete="cascade", reverse=False),
        "tag_id": Reference("tags", on_delete="cascade", reverse=False),
    }
    link = ("invoice_id", "tag_id")  # invoices get `tags`, tags get `invoices`
    hooks = {"before_create": [ensure_not_tagged]}
    filterable = ["invoice_id", "tag_id"]
