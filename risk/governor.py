from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .core import (
    AccountSnapshot,
    CircuitState,
    D,
    DecisionStatus,
    IntentKind,
    OrderIntent,
    RiskDecision,
    RiskPolicy,
    ZERO,
    quantize_size,
    utc_now,
)


@dataclass(frozen=True)
class MarketSnapshot:
    metadata_hash: str
    feed_age_ms: int
    clock_skew_ms: int = 0
    book_age_ms: int = 0


class RiskGovernor:
    """Pure order-admission layer. It does not sign, submit, or cancel orders."""

    def __init__(self, policy: RiskPolicy):
        policy.validate()
        self.policy = policy

    def evaluate(
        self,
        intent: OrderIntent,
        account: AccountSnapshot,
        market: MarketSnapshot,
        now: datetime | None = None,
    ) -> RiskDecision:
        decided_at = now or utc_now()
        reasons: list[str] = []
        is_reduction = intent.intent_kind == IntentKind.REDUCE_ONLY

        if account.circuit_state == CircuitState.HALTED:
            return self._reject("HALTED", decided_at, halt=True)
        if account.circuit_state in {CircuitState.PAUSE_NEW, CircuitState.UNWIND_ONLY} and not is_reduction:
            return self._reject("CIRCUIT_BLOCKS_NEW_ENTRY", decided_at)

        if not account.reconciled:
            return self._reject("ACCOUNT_NOT_RECONCILED", decided_at)
        if market.feed_age_ms > self.policy.max_feed_age_ms:
            return self._reject("STALE_FEED", decided_at)
        if market.book_age_ms > self.policy.max_feed_age_ms:
            return self._reject("STALE_ORDER_BOOK", decided_at)
        if market.clock_skew_ms > self.policy.max_clock_skew_ms:
            return self._reject("CLOCK_SKEW", decided_at)
        if intent.snapshot_metadata_hash != market.metadata_hash:
            return self._reject("METADATA_HASH_CHANGED", decided_at)
        if intent.resolution_risk.upper() == "AMBIGUOUS":
            return self._reject("AMBIGUOUS_RESOLUTION", decided_at)
        if intent.size <= ZERO or intent.price <= ZERO:
            return self._reject("NON_POSITIVE_SIZE_OR_PRICE", decided_at)

        if not is_reduction and intent.edge_after_costs <= self.policy.minimum_edge:
            return self._reject("INSUFFICIENT_AFTER_COST_EDGE", decided_at)
        if not is_reduction and intent.residual_leg_loss > self.policy.max_residual_leg_loss:
            return self._reject("RESIDUAL_LEG_LIMIT", decided_at)

        requested = intent.notional
        if requested > self.policy.max_order_notional and not is_reduction:
            return self._reject("ORDER_NOTIONAL_LIMIT", decided_at)

        reservation = intent.reservation
        projected = account.projected(intent, reservation)
        if not is_reduction and projected.free_cash < self.policy.safety_cash_buffer:
            return self._reject("SAFETY_CASH_BUFFER", decided_at, projected)
        if not is_reduction and account.reserved_cash + reservation > self.policy.max_reserved_notional:
            return self._reject("RESERVED_NOTIONAL_LIMIT", decided_at, projected)
        if not is_reduction and projected.market_loss > self.policy.max_market_loss:
            return self._reject("MARKET_LOSS_LIMIT", decided_at, projected)
        if not is_reduction and projected.event_loss > self.policy.max_event_loss:
            return self._reject("EVENT_LOSS_LIMIT", decided_at, projected)
        if not is_reduction and projected.total_loss > self.policy.max_total_loss:
            return self._reject("TOTAL_LOSS_LIMIT", decided_at, projected)
        if not is_reduction and account.daily_loss >= self.policy.hard_daily_loss:
            return self._reject("HARD_DAILY_LOSS", decided_at, projected)
        if not is_reduction and account.rolling_loss >= self.policy.rolling_loss:
            return self._reject("ROLLING_LOSS", decided_at, projected)
        if not is_reduction and account.current_drawdown >= self.policy.max_drawdown:
            return self._reject("MAX_DRAWDOWN", decided_at, projected)

        multiplier = Decimal("1")
        status = DecisionStatus.ALLOW
        if account.circuit_state == CircuitState.CAUTION:
            multiplier = self.policy.caution_size_multiplier
            reasons.append("CAUTION_SIZE_REDUCED")
        elif account.circuit_state == CircuitState.UNWIND_ONLY:
            multiplier = self.policy.unwind_size_multiplier
            status = DecisionStatus.REDUCE_ONLY

        if account.daily_loss >= self.policy.soft_daily_loss and not is_reduction:
            multiplier = min(multiplier, self.policy.caution_size_multiplier)
            reasons.append("SOFT_DAILY_LOSS_SIZE_REDUCED")

        safe_size = intent.size * multiplier
        safe_size = min(safe_size, self.policy.max_order_notional / intent.price)
        safe_size = min(safe_size, max(ZERO, account.free_cash - self.policy.safety_cash_buffer) / intent.price)
        if intent.liquidity_safe_size is not None:
            safe_size = min(safe_size, intent.liquidity_safe_size)
        safe_size = quantize_size(safe_size)
        if safe_size <= ZERO:
            return self._reject("ZERO_SAFE_SIZE", decided_at, projected)

        return RiskDecision(
            status=status,
            approved_size=safe_size,
            reason_codes=tuple(reasons) or ("ALL_CHECKS_PASSED",),
            policy_version=self.policy.version,
            decided_at=decided_at,
            projected=projected,
        )

    def _reject(
        self,
        reason: str,
        decided_at: datetime,
        projected=None,
        halt: bool = False,
    ) -> RiskDecision:
        return RiskDecision(
            status=DecisionStatus.HALT if halt else DecisionStatus.REJECT,
            approved_size=ZERO,
            reason_codes=(reason,),
            policy_version=self.policy.version,
            decided_at=decided_at,
            projected=projected,
        )


class CircuitBreaker:
    """Minimal state machine with fail-closed transitions."""

    SEVERE_TRIGGERS = {
        "UNKNOWN_FILL",
        "RECONCILIATION_MISMATCH",
        "KEY_ANOMALY",
        "LEDGER_CORRUPTION",
        "UNEXPECTED_DESTINATION",
    }

    def __init__(self, state: CircuitState = CircuitState.NORMAL):
        self.state = state
        self.last_reason: str | None = None

    def trigger(self, reason: str) -> CircuitState:
        self.last_reason = reason
        if reason in self.SEVERE_TRIGGERS:
            self.state = CircuitState.HALTED
        elif reason in {"DRAWDOWN_BREACH", "RESIDUAL_LEG_BREACH"}:
            self.state = CircuitState.UNWIND_ONLY
        else:
            self.state = CircuitState.PAUSE_NEW
        return self.state

    def clear_after_reconciliation(self, manual_reset: bool = False) -> CircuitState:
        if self.state == CircuitState.HALTED and not manual_reset:
            return self.state
        if self.state in {CircuitState.PAUSE_NEW, CircuitState.UNWIND_ONLY, CircuitState.HALTED}:
            if manual_reset:
                self.state = CircuitState.CAUTION
        return self.state

    def set_caution(self) -> CircuitState:
        if self.state == CircuitState.NORMAL:
            self.state = CircuitState.CAUTION
        return self.state
