from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from risk.core import D, ZERO


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Quote:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class OrderBook:
    market_id: str
    token_id: str
    bids: tuple[Quote, ...]
    asks: tuple[Quote, ...]
    captured_at: datetime = field(default_factory=utc_now)
    metadata_hash: str = ""

    @property
    def best_bid(self) -> Quote | None:
        return max(self.bids, key=lambda q: q.price, default=None)

    @property
    def best_ask(self) -> Quote | None:
        return min(self.asks, key=lambda q: q.price, default=None)

    def executable_buy_cost(self, quantity: Decimal) -> Decimal | None:
        remaining = quantity
        total = ZERO
        for level in sorted(self.asks, key=lambda q: q.price):
            take = min(remaining, level.size)
            total += take * level.price
            remaining -= take
            if remaining <= ZERO:
                return total
        return None

    def executable_sell_value(self, quantity: Decimal) -> Decimal | None:
        remaining = quantity
        total = ZERO
        for level in sorted(self.bids, key=lambda q: q.price, reverse=True):
            take = min(remaining, level.size)
            total += take * level.price
            remaining -= take
            if remaining <= ZERO:
                return total
        return None

    def depth_within(self, side: str, limit_price: Decimal) -> Decimal:
        if side.upper() == "BUY":
            return sum((q.size for q in self.asks if q.price <= limit_price), ZERO)
        return sum((q.size for q in self.bids if q.price >= limit_price), ZERO)


@dataclass(frozen=True)
class Market:
    market_id: str
    event_id: str
    question: str
    outcome: str
    token_id: str
    resolution_source: str
    resolution_risk: str = "VERIFIED"
    correlation_group: str = ""
    active: bool = True
    neg_risk: bool = False
    augmented_neg_risk: bool = False
    metadata_hash: str = ""


@dataclass(frozen=True)
class MarketSnapshot:
    market: Market
    order_book: OrderBook
    midpoint: Decimal
    captured_at: datetime = field(default_factory=utc_now)

    @property
    def age_ms(self) -> int:
        now = utc_now()
        return max(0, int((now - self.captured_at).total_seconds() * 1000))


@dataclass(frozen=True)
class Signal:
    signal_id: str
    strategy: str
    market: Market
    side: str
    price: Decimal
    size: Decimal
    gross_edge: Decimal
    estimated_fees: Decimal
    estimated_slippage: Decimal
    model_uncertainty_penalty: Decimal
    confidence: Decimal
    residual_leg_loss: Decimal = ZERO
    rationale: str = ""
    created_at: datetime = field(default_factory=utc_now)

    @property
    def edge_after_costs(self) -> Decimal:
        return self.gross_edge - self.estimated_fees - self.estimated_slippage - self.model_uncertainty_penalty


@dataclass(frozen=True)
class SimulationSummary:
    trials: int
    mean_return: Decimal
    p05_return: Decimal
    p50_return: Decimal
    p95_return: Decimal
    loss_probability: Decimal
    max_loss: Decimal
    max_gain: Decimal
    stable: bool
    profile_name: str = "standard"


@dataclass(frozen=True)
class Decision:
    signal: Signal
    simulation: SimulationSummary
    approved: bool
    reason: str
    decided_at: datetime = field(default_factory=utc_now)


def decimal_mean(values: Iterable[Decimal]) -> Decimal:
    values = tuple(values)
    return sum(values, ZERO) / Decimal(len(values)) if values else ZERO
