"""Operation and job metrics with the OpenTelemetry API.

``OperationMetrics`` (an ``OperationObserver``, meter ``resource_engine``):

- ``resource_engine.operation.duration`` (histogram, ``s``): every manager
  operation, from the call to its return. Attributes:
  ``resource_engine.resource``, ``resource_engine.operation``,
  ``resource_engine.outcome`` (``ok``, ``failed``), ``error.type`` (the error
  code or class name, when failed) and, optionally, ``resource_engine.action``.

``JobMetrics`` (a ``JobObserver``, meter ``resource_engine.jobs``):

- ``resource_engine.job.runs`` (counter, ``{run}``): every run, by
  ``resource_engine.job`` and ``resource_engine.outcome`` (``ok``, ``failed``,
  ``skipped``);
- ``resource_engine.job.duration`` (histogram, ``s``): runs that ran (not
  ``skipped``).

Only the API is required. Without an SDK and exporter configured, the
instruments are no-ops.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import metrics
from resource_engine.ports.observability import OperationRun


def _meter(name: str, meter_provider: Any | None) -> Any:
    from resource_engine import __version__

    return metrics.get_meter(name, __version__, meter_provider=meter_provider)


class OperationMetrics:
    """Pass to ``ResourceManager(observers=[OperationMetrics()])``.

    Args:
        meter_provider: Defaults to the global provider.
        include_action: Add ``resource_engine.action`` (``transition:approve``,
            ``action:send``). Turn it off when actions are many (cardinality).
    """

    def __init__(self, meter_provider: Any | None = None, *, include_action: bool = True) -> None:
        meter = _meter("resource_engine", meter_provider)
        self.include_action = include_action
        self.duration = meter.create_histogram(
            "resource_engine.operation.duration",
            unit="s",
            description="Duration of Resource Engine operations, by resource, operation "
            "and outcome.",
        )

    def __call__(self, run: OperationRun) -> None:
        attributes: dict[str, str] = {
            "resource_engine.resource": run.resource,
            "resource_engine.operation": run.operation,
            "resource_engine.outcome": run.outcome,
        }
        if run.error_type:
            attributes["error.type"] = run.error_type
        if self.include_action and run.action:
            attributes["resource_engine.action"] = run.action
        self.duration.record(run.seconds, attributes)


class JobMetrics:
    """Pass to ``JobRunner(observers=[JobMetrics()])``.

    Args:
        meter_provider: Defaults to the global provider.
    """

    def __init__(self, meter_provider: Any | None = None) -> None:
        meter = _meter("resource_engine.jobs", meter_provider)
        self.runs = meter.create_counter(
            "resource_engine.job.runs",
            unit="{run}",
            description="Runs of Resource Engine background jobs, by outcome.",
        )
        self.duration = meter.create_histogram(
            "resource_engine.job.duration",
            unit="s",
            description="Duration of background job runs that ran (not skipped).",
        )

    def __call__(self, job: str, outcome: str, seconds: float, error: BaseException | None) -> None:
        attributes = {"resource_engine.job": job, "resource_engine.outcome": outcome}
        if error is not None:
            attributes["error.type"] = getattr(error, "code", None) or type(error).__name__
        self.runs.add(1, attributes)
        if outcome != "skipped":
            self.duration.record(seconds, attributes)
