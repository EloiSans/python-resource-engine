from __future__ import annotations

from resource_engine import Resource

from helpdesk.customers.application import hooks
from helpdesk.customers.application.schemas import CustomerCreate, CustomerRead, CustomerUpdate
from helpdesk.customers.domain.entities import Customer


class CustomerResource(Resource):
    """Customers of the help desk: who tickets are opened for.

    Business area:
        Support
    """

    name = "customers"
    model = Customer
    schemas = {"create": CustomerCreate, "update": CustomerUpdate, "read": CustomerRead}
    operations = ["list", "get", "create", "update", "delete"]
    operation_docs = {
        "list": "Lists the customers.",
        "get": "Returns a customer.",
        "create": "Registers a customer.",
        "update": "Changes a customer's name or e-mail.",
        "delete": "Removes a customer.",
    }
    hooks = {"before_create": [hooks.validate], "after_update": [hooks.validate]}
    sortable = ["id"]
