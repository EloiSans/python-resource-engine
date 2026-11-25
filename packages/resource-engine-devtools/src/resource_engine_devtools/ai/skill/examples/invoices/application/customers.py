from __future__ import annotations

from resource_engine import Resource

from invoices.application.schemas import CustomerCreate, CustomerRead, CustomerUpdate
from invoices.domain.entities import Customer


class CustomerResource(Resource):
    """Clients als quals l'organització emet factures.

    Les factures hi fan referència amb ``customer_id``; un client amb factures
    no es pot eliminar.

    Business area:
        Facturació

    Actors:
        - Administrador
        - Emissor de factures
    """

    model = Customer
    schemas = {"create": CustomerCreate, "update": CustomerUpdate, "read": CustomerRead}
    operations = ["list", "get", "create", "update", "delete"]
    operation_docs = {
        "list": "Llista els clients, filtrables per país i nom.",
        "get": "Consulta un client.",
        "create": "Dona d'alta un client.",
        "update": "Modifica les dades d'un client.",
        "delete": "Elimina un client que no té factures.",
    }
    filterable = ["country", "name", "tax_id"]
    sortable = ["id", "name"]
