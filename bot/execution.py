from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import Signal


@dataclass(frozen=True)
class ExecutionResult:
    accepted: bool
    order_id: str | None
    reason: str


class ExecutionAdapter:
    def submit(self, signal: Signal, approved_size: Decimal) -> ExecutionResult:
        raise NotImplementedError

    def cancel_all(self) -> None:
        raise NotImplementedError


class DisabledLiveExecution(ExecutionAdapter):
    """Safe placeholder: no live order can be submitted by accident."""

    def submit(self, signal: Signal, approved_size: Decimal) -> ExecutionResult:
        return ExecutionResult(False, None, "LIVE_EXECUTION_DISABLED")

    def cancel_all(self) -> None:
        return None
