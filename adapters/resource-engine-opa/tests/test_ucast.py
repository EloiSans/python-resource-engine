import pytest
from resource_engine.errors import ConditionNotSupported
from resource_engine_opa import ucast_condition


def test_expanded_nodes():
    node = {
        "type": "compound",
        "operator": "or",
        "value": [
            {
                "type": "compound",
                "operator": "and",
                "value": [
                    {"type": "field", "field": "resource.owner", "operator": "eq", "value": "u1"},
                    {
                        "type": "compound",
                        "operator": "not",
                        "value": [
                            {
                                "type": "field",
                                "field": "resource.status",
                                "operator": "eq",
                                "value": "void",
                            }
                        ],
                    },
                ],
            },
            {"type": "field", "field": "input.amount", "operator": "lte", "value": 10},
            {
                "type": "field",
                "field": "resource.a",
                "operator": "ne",
                "value": {"field": "resource.b"},
            },
        ],
    }
    assert ucast_condition(node) == {
        "or": [
            {
                "and": [
                    {"eq": [{"attr": "resource.owner"}, "u1"]},
                    {"not": {"eq": [{"attr": "resource.status"}, "void"]}},
                ]
            },
            {"le": [{"attr": "input.amount"}, 10]},
            {"ne": [{"attr": "resource.a"}, {"attr": "resource.b"}]},
        ]
    }


def test_no_condition_and_concise_form():
    assert ucast_condition({}) is True
    assert ucast_condition({"resource.a": 1, "resource.b": {"in": [1, 2]}}) == {
        "and": [{"eq": [{"attr": "resource.a"}, 1]}, {"in": [{"attr": "resource.b"}, [1, 2]]}]
    }


def test_unsupported_operators_fail_closed():
    with pytest.raises(ConditionNotSupported):
        ucast_condition(
            {"type": "field", "field": "resource.n", "operator": "startswith", "value": "a"}
        )
