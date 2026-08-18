from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, getcontext
from enum import Enum
from typing import Optional

getcontext().prec = 28

ZERO = Decimal("0")
ONE = Decimal("1")


def D(value: Decimal | int | float | str) -> Decimal:
    """Convert values to Decimal without inheriting binary float artifacts."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CircuitState(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    PAUSE_NEW = "PAUSE_NEW"
    UNWIND_ONLY = "UNWIND_ONLY"
    HALTED = "HALTED"


class IntentKind(str, Enum):
    ENTRY = "ENTRY"
    HEDGE = "HEDGE"
    REDUCE_ONLY = "REDUCE_ONLY"


class DecisionStatus(str, Enum):
    ALLOW = "ALLOW"
    REDUCE_ONLY = "REDUCE_ONLY"
    REJECT = "REJECT"
    HALT = "HALT"


@dataclass(frozen=True)
class RiskPolicy:
    """Versioned policy. Keep values immutable during an execution session."""

    version: str = "rm-1.0-initial"
    live_cap: Decimal = Decimal("10")
    max_reserved_notional: Decimal = Decimal("6")
    max_order_notional: Decimal = Decimal("1")
    max_market_loss: Decimal = Decimal("2")
    max_event_loss: Decimal = Decimal("3")
    max_total_loss: Decimal = Decimal("8")
    max_residual_leg_loss: Decimal = Decimal("0.50")
    safety_cash_buffer: Decimal = Decimal("2")
    soft_daily_loss: Decimal = Decimal("0.25")
    hard_daily_loss: Decimal = Decimal("0.50")
    rolling_loss: Decimal = Decimal("1")
    max_drawdown: Decimal = Decimal("2")
    minimum_edge: Decimal = Decimal("0.005")
    max_feed_age_ms: int = 3_000
    max_clock_skew_ms: int = 2_000
    fractional_kelly: Decimal = Decimal("0.125")
    caution_size_multiplier: Decimal = Decimal("0.5")
    unwind_size_multiplier: Decimal = Decimal("0.25")

    def validate(self) -> None:
        if self.live_cap <= ZERO:
            raise ValueError("live_cap must be positive")
        if self.max_order_notional > self.live_cap:
            raise ValueError("max_order_notional cannot exceed live_cap")
        if not ZERO < self.fractional_kelly <= Decimal("0.25"):
            raise ValueError("fractional_kelly must be in (0, 0.25]")
        if self.safety_cash_buffer >= self.live_cap:
            raise ValueError("safety_cash_buffer must be below live_cap")
        for name in ("max_reserved_notional", "max_market_loss", "max_event_loss", "max_total_loss"):
            if getattr(self, name) <= ZERO:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class Position:
    market_id: str
    event_id: str
    correlation_group: str
    token_id: str
    side: str
    quantity: Decimal
    entry_cost: Decimal
    fee_paid: Decimal = ZERO
    conservative_exit_value: Decimal = ZERO
    stress_exit_value: Decimal = ZERO
    resolution_risk: str = "VERIFIED"

    @property
    def worst_case_loss(self) -> Decimal:
        gross_cost = self.entry_cost + self.fee_paid
        return max(ZERO, gross_cost - self.stress_exit_value)

    @property
    def market_loss(self) -> Decimal:
        return self.worst_case_loss


@dataclass(frozen=True)
class OpenOrder:
    order_id: str
    market_id: str
    event_id: str
    correlation_group: str
    token_id: str
    side: str
    price: Decimal
    remaining_size: Decimal
    fee_reserve: Decimal = ZERO
    slippage_reserve: Decimal = ZERO
    residual_leg_reserve: Decimal = ZERO

    @property
    def notional(self) -> Decimal:
        return self.price * self.remaining_size

    @property
    def reservation(self) -> Decimal:
        return self.notional + self.fee_reserve + self.slippage_reserve + self.residual_leg_reserve


@dataclass(frozen=True)
class AccountSnapshot:
    cash: Decimal
    positions: tuple[Position, ...] = ()
    open_orders: tuple[OpenOrder, ...] = ()
    realized_pnl_today: Decimal = ZERO
    realized_pnl_rolling: Decimal = ZERO
    fees_today: Decimal = ZERO
    settlement_losses: Decimal = ZERO
    high_water_mark: Decimal = ZERO
    circuit_state: CircuitState = CircuitState.NORMAL
    reconciled: bool = True
    feed_age_ms: int = 0
    clock_skew_ms: int = 0
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def reserved_cash(self) -> Decimal:
        return sum((o.reservation for o in self.open_orders if o.side.upper() == "BUY"), ZERO)

    @property
    def conservative_position_value(self) -> Decimal:
        return sum((p.conservative_exit_value for p in self.positions), ZERO)

    @property
    def conservative_equity(self) -> Decimal:
        return self.cash + self.conservative_position_value - self.settlement_losses

    @property
    def free_cash(self) -> Decimal:
        return self.cash - self.reserved_cash

    @property
    def current_drawdown(self) -> Decimal:
        if self.high_water_mark <= ZERO:
            return ZERO
        return max(ZERO, self.high_water_mark - self.conservative_equity)

    @property
    def daily_loss(self) -> Decimal:
        return max(ZERO, -self.realized_pnl_today) + self.fees_today + self.settlement_losses

    @property
    def rolling_loss(self) -> Decimal:
        return max(ZERO, -self.realized_pnl_rolling) + self.fees_today + self.settlement_losses

    def positions_for_market(self, market_id: str) -> tuple[Position, ...]:
        return tuple(p for p in self.positions if p.market_id == market_id)

    def positions_for_event(self, event_id: str) -> tuple[Position, ...]:
        return tuple(p for p in self.positions if p.event_id == event_id)

    def projected(self, intent: "OrderIntent", reservation: Decimal) -> "ProjectedExposure":
        market_loss = sum((p.market_loss for p in self.positions_for_market(intent.market_id)), ZERO)
        event_loss = sum((p.worst_case_loss for p in self.positions_for_event(intent.event_id)), ZERO)
        current_total = sum((p.worst_case_loss for p in self.positions), ZERO)
        projected_market = market_loss + intent.projected_loss + reservation
        projected_event = event_loss + intent.projected_loss + reservation
        projected_total = current_total + intent.projected_loss + reservation
        return ProjectedExposure(
            free_cash=self.free_cash - reservation,
            market_loss=projected_market,
            event_loss=projected_event,
            total_loss=projected_total,
            residual_leg_loss=intent.residual_leg_loss,
        )


@dataclass(frozen=True)
class OrderIntent:
    market_id: str
    event_id: str
    correlation_group: str
    token_id: str
    side: str
    price: Decimal
    size: Decimal
    edge_after_costs: Decimal
    fee_reserve: Decimal = ZERO
    slippage_reserve: Decimal = ZERO
    residual_leg_loss: Decimal = ZERO
    intent_kind: IntentKind = IntentKind.ENTRY
    resolution_risk: str = "VERIFIED"
    market_metadata_hash: str = ""
    snapshot_metadata_hash: str = ""
    book_age_ms: int = 0
    liquidity_safe_size: Optional[Decimal] = None

    @property
    def notional(self) -> Decimal:
        return self.price * self.size

    @property
    def projected_loss(self) -> Decimal:
        return max(ZERO, self.notional + self.fee_reserve + self.residual_leg_loss)

    @property
    def reservation(self) -> Decimal:
        return self.notional + self.fee_reserve + self.slippage_reserve + self.residual_leg_loss


@dataclass(frozen=True)
class ProjectedExposure:
    free_cash: Decimal
    market_loss: Decimal
    event_loss: Decimal
    total_loss: Decimal
    residual_leg_loss: Decimal


@dataclass(frozen=True)
class RiskDecision:
    status: DecisionStatus
    approved_size: Decimal
    reason_codes: tuple[str, ...]
    policy_version: str
    decided_at: datetime
    projected: Optional[ProjectedExposure] = None

    @property
    def allowed(self) -> bool:
        return self.status in {DecisionStatus.ALLOW, DecisionStatus.REDUCE_ONLY} and self.approved_size > ZERO


def quantize_size(size: Decimal, lot_size: Decimal = Decimal("0.01")) -> Decimal:
    if lot_size <= ZERO:
        raise ValueError("lot_size must be positive")
    return (size / lot_size).to_integral_value(rounding=ROUND_DOWN) * lot_size
