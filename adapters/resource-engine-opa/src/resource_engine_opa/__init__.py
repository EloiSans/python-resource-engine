"""Open Policy Agent for Resource Engine (see docs/authorizers.md)."""

from resource_engine_opa.engine import OpaEngine
from resource_engine_opa.ucast import ucast_condition

__all__ = ["OpaEngine", "ucast_condition"]
