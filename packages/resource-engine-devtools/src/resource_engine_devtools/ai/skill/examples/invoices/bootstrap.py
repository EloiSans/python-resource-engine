"""Composition root of the example application."""

from __future__ import annotations

from resource_engine import ResourceRegistry
from resource_engine.authz import AuthzConfig, IdpRoleSource, SystemRole
from resource_engine.explorer import register_explorer

from invoices.application.balances import CustomerBalanceResource
from invoices.application.customers import CustomerResource
from invoices.application.processes import InvoicePaymentProcess
from invoices.application.resource import InvoiceResource
from invoices.application.tags import InvoiceTagResource, TagResource

#: Records of these resources belong to an organization when tenancy is on.
TENANT_FIELD = "organization"
MODULE = (
    CustomerResource,
    InvoiceResource,
    TagResource,
    InvoiceTagResource,
    InvoicePaymentProcess,
    CustomerBalanceResource,
)


def build_registry(*, tenant_scoped: bool = False) -> ResourceRegistry:
    """The module's resources and the engine's (documents, permissions, audit).

    With ``tenant_scoped`` every module resource is confined to the caller's
    organization (``tenant_field = "organization"``, from the Keycloak token):
    the application (``invoices.app``) runs that way. Without it the same
    resources ignore tenants, which keeps engine tests short.
    """
    registry = ResourceRegistry()
    for resource in MODULE:
        registry.register(_tenant_scoped(resource) if tenant_scoped else resource)
    registry.register_documents()
    registry.register_authz()
    registry.register_api_keys()  # keys for machine clients (Bearer rek_...)
    registry.register_audit()
    # the explorer's permissions (events:read, jobs:read, jobs:run...): the admin
    # role holds them; publishing events by hand is granted to no one
    register_explorer(registry)
    return registry


def _tenant_scoped(resource: type) -> type:
    return type(
        resource.__name__,
        (resource,),
        {
            "__doc__": resource.__doc__,
            "__module__": resource.__module__,
            "name": resource.definition().name,
            "tenant_field": TENANT_FIELD,
        },
    )


registry = build_registry()
#: What the application serves: every module record belongs to an organization.
tenant_registry = build_registry(tenant_scoped=True)

# Permissions: system roles are fixed here; everything else is policies created
# from the front end (see invoices.authz for the seed used by tests and demos).
authz = AuthzConfig(
    system_roles=[SystemRole.admin(), SystemRole.viewer()],
    role_source=IdpRoleSource(
        {
            "invoices-admin": "admin",
            "accountant": "viewer",
            "approver": "approver",
            "issuer": "issuer",
        }
    ),
    bootstrap={"platform-admin": "admin"},
)
