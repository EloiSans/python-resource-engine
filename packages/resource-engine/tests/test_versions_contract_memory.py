"""The version contract against the in-memory store (the reference)."""

import pytest
from resource_engine.testing import InMemoryStore
from resource_engine.testing.contracts import VersionContract, contract_registry


class TestInMemoryVersions(VersionContract):
    @pytest.fixture
    def contract_uow_factory(self):
        return InMemoryStore.from_registry(contract_registry()).unit_of_work
