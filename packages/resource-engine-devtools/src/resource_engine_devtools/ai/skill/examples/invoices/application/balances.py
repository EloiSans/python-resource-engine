from __future__ import annotations

from resource_engine import Aggregate, Count, Max, Reference, Resource, Sum

from invoices.application.schemas import CustomerBalanceRead
from invoices.domain.entities import CustomerBalance


class CustomerBalanceResource(Resource):
    """Saldo de facturació de cada client, calculat a partir de les factures.

    És una vista: només es consulta, i cada fila es recalcula amb les factures
    del client (no es desa enlloc).

    Business area:
        Facturació

    Actors:
        - Administrador
        - Emissor de factures
    """

    name = "customer_balances"
    model = CustomerBalance
    schemas = {"read": CustomerBalanceRead}
    operations = ["list", "get"]
    operation_docs = {
        "list": "Llista els saldos dels clients amb factures, ordenables per import.",
        "get": "Consulta el saldo d'un client.",
    }
    id_field = "customer_id"
    # read-only rows computed from invoices: one GROUP BY query in SQL
    view = Aggregate(
        "invoices",
        group_by=["customer_id", "organization"],
        values={
            "invoices": Count(),
            "total": Sum("amount"),
            "outstanding": Sum("amount", where={"status": ["pending", "approved"]}),
            "paid": Sum("amount", where={"status": "paid"}),
            "last_issue_date": Max("issue_date"),
        },
    )
    references = {
        "customer_id": Reference(
            "customers", name="customer", related_name="balance", description="El client."
        )
    }
    filterable = ["customer_id"]
    sortable = ["total", "outstanding", "paid", "last_issue_date"]
