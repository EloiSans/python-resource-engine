from __future__ import annotations

from helpdesk.tickets.application import hooks
from helpdesk.tickets.application.schemas import TicketCreate, TicketRead, TicketUpdate
from helpdesk.tickets.domain.entities import Ticket
from helpdesk.tickets.domain.state_machine import TicketStateMachine
from resource_engine import Resource


class TicketResource(Resource):
    """Support tickets: opened for a customer, worked on and resolved by agents.

    Business area:
        Support
    """

    name = "tickets"
    model = Ticket
    schemas = {"create": TicketCreate, "update": TicketUpdate, "read": TicketRead}
    operations = ["list", "get", "create", "update"]
    operation_docs = {
        "list": "Lists the tickets, filterable by state and customer.",
        "get": "Returns a ticket.",
        "create": "Opens a ticket for a customer.",
        "update": "Changes the subject or the description.",
    }
    hooks = {"before_create": [hooks.validate], "after_update": [hooks.validate]}
    filterable = ["status", "customer_id"]
    sortable = ["id"]
    state_machine = TicketStateMachine
