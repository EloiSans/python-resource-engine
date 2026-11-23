# Example used by the adapter tests (resource "invoices").
package resource_engine

import rego.v1

allow if {
	input.action == "invoices:create"
	"clerk" in input.subject.roles
	input.input.amount <= 1000
}

allow if {
	input.action in {"invoices:list", "invoices:get"}
	"clerk" in input.subject.roles
	input.resource.owner == input.subject.id
	not input.resource.status == "void"
}

allow if {
	input.action in {"invoices:list", "invoices:get"}
	"clerk" in input.subject.roles
	input.resource.amount < 10
	input.resource.status != "void"
}

allow if {
	input.action == "invoices:list"
	"auditor" in input.subject.roles
	"urgent" in input.resource.tags
}
