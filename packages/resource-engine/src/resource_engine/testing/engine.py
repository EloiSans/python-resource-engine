"""``ConditionEngine``: an in-memory ``PolicyEngine`` for tests and prototypes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from resource_engine.authz.catalog import action_matches
from resource_engine.authz.conditions import Env, disjunction, partial
from resource_engine.ports.authz import AuthzRequest


class ConditionEngine:
    """Rules as ``{action pattern: condition}`` in the engine-neutral condition
    language (``True`` allows unconditionally). Deny by default; several
    matching rules are OR-ed. Records the requests it answers in ``requests``.
    """

    def __init__(self, rules: Mapping[str, Any]) -> None:
        self.rules = dict(rules)
        self.requests: list[AuthzRequest] = []

    async def evaluate(self, request: AuthzRequest) -> Any:
        self.requests.append(request)
        if request.subject is None:
            return False
        namespaces: dict[str, Any] = {"subject": request.subject, "context": request.context}
        if request.resource is not ...:
            namespaces["resource"] = request.resource
        if request.input is not ...:
            namespaces["input"] = request.input
        env = Env(**namespaces)
        return disjunction(
            *(
                partial(None if condition is True else condition, env)
                for pattern, condition in self.rules.items()
                if action_matches(pattern, request.action)
            )
        )
