from __future__ import annotations

import hashlib
from decimal import Decimal
from itertools import combinations

from .models import MarketSnapshot, Signal
from risk.core import D, ZERO


class StructuralScanner:
    """Find only mechanically-defined relationships; it does not predict outcomes."""

    def __init__(
        self,
        min_edge: Decimal = Decimal("0.01"),
        fee_rate: Decimal = Decimal("0"),
        max_book_age_ms: int = 3_000,
        min_depth: Decimal = Decimal("0.10"),
    ):
        self.min_edge = min_edge
        self.fee_rate = fee_rate
        self.max_book_age_ms = max_book_age_ms
        self.min_depth = min_depth

    def scan_binary_parity(
        self,
        yes: MarketSnapshot,
        no: MarketSnapshot,
        max_size: Decimal = Decimal("1"),
    ) -> list[Signal]:
        if not self._compatible(yes, no):
            return []
        yes_ask = yes.order_book.best_ask
        no_ask = no.order_book.best_ask
        if yes_ask is None or no_ask is None:
            return []
        size = min(max_size, yes_ask.size, no_ask.size)
        if size < self.min_depth:
            return []
        total_cost = yes_ask.price + no_ask.price
        gross_edge = Decimal("1") - total_cost
        fees = total_cost * self.fee_rate
        slippage = Decimal("0.001")
        edge = gross_edge - fees - slippage
        if edge < self.min_edge:
            return []
        signal_id = self._id("binary", yes.market.market_id, no.market.market_id, yes.order_book.captured_at.isoformat())
        return [
            Signal(
                signal_id=signal_id,
                strategy="binary_parity",
                market=yes.market,
                side="BUY_PAIR",
                price=total_cost,
                size=size,
                gross_edge=gross_edge,
                estimated_fees=fees,
                estimated_slippage=slippage,
                model_uncertainty_penalty=Decimal("0.002"),
                confidence=Decimal("0.95"),
                residual_leg_loss=Decimal("0.05"),
                rationale="YES ask + NO ask below one-dollar payout, subject to both legs filling.",
            )
        ]

    def scan_multi_outcome(
        self,
        outcomes: tuple[MarketSnapshot, ...],
        max_size: Decimal = Decimal("1"),
    ) -> list[Signal]:
        if len(outcomes) < 2 or any(not s.market.active for s in outcomes):
            return []
        first = outcomes[0]
        if any(not self._compatible(first, candidate) for candidate in outcomes[1:]):
            return []
        asks = [s.order_book.best_ask for s in outcomes]
        if any(ask is None for ask in asks):
            return []
        assert all(ask is not None for ask in asks)
        size = min([max_size, *(ask.size for ask in asks)])
        if size < self.min_depth:
            return []
        total_cost = sum((ask.price for ask in asks), ZERO)
        gross_edge = Decimal("1") - total_cost
        fees = total_cost * self.fee_rate
        slippage = Decimal("0.001") * D(len(outcomes))
        edge = gross_edge - fees - slippage
        if edge < self.min_edge:
            return []
        signal_id = self._id("multi", *(s.market.market_id for s in outcomes))
        return [
            Signal(
                signal_id=signal_id,
                strategy="multi_outcome_completeness",
                market=first.market,
                side="BUY_COMPLETE_SET",
                price=total_cost,
                size=size,
                gross_edge=gross_edge,
                estimated_fees=fees,
                estimated_slippage=slippage,
                model_uncertainty_penalty=Decimal("0.003"),
                confidence=Decimal("0.90"),
                residual_leg_loss=Decimal("0.10"),
                rationale="Sum of executable outcome asks below one-dollar complete-set payout.",
            )
        ]

    def _compatible(self, left: MarketSnapshot, right: MarketSnapshot) -> bool:
        if left.market.event_id != right.market.event_id:
            return False
        if left.market.resolution_source != right.market.resolution_source:
            return False
        if left.market.resolution_risk.upper() == "AMBIGUOUS" or right.market.resolution_risk.upper() == "AMBIGUOUS":
            return False
        if left.market.augmented_neg_risk or right.market.augmented_neg_risk:
            return False
        if left.age_ms > self.max_book_age_ms or right.age_ms > self.max_book_age_ms:
            return False
        return True

    @staticmethod
    def _id(kind: str, *parts: str) -> str:
        return hashlib.sha256((kind + ":" + ":".join(parts)).encode()).hexdigest()[:20]
