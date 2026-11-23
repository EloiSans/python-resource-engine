# Rules of resource_engine.testing.contracts.AuthorizerContract (contract_docs).
# Decisions: data.resource_engine.allow; row filters: the compile API on the
# same rule with input.resource unknown. No `default allow` (compile refuses it).
package resource_engine

import rego.v1

owner_actions := {"contract_docs:get", "contract_docs:list", "contract_docs:update"}

allow if {
	owner_actions[input.action]
	input.resource.owner == input.subject.id
}

allow if {
	input.action == "contract_docs:create"
	"writer" in input.subject.roles
}

allow if {
	input.action == "contract_docs:read:secret"
	"auditor" in input.subject.roles
}
