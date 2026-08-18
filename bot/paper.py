from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import Decision, Signal
from risk.core import D, ZERO


class DecimalJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class EventLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "event_type": event_type,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, cls=DecimalJSONEncoder, sort_keys=True) + "\n")


@dataclass
class PaperPosition:
    market_id: str
    token_id: str
    quantity: Decimal
    average_price: Decimal
    cost: Decimal


class PaperBroker:
    """Pessimistic paper broker: no assumed fills above available signal size."""

    def __init__(self, starting_cash: Decimal, ledger: EventLedger):
        self.cash = D(starting_cash)
        self.starting_cash = self.cash
        self.ledger = ledger
        self.positions: dict[str, PaperPosition] = {}

    def submit(self, decision: Decision) -> bool:
        if not decision.approved or decision.signal.size <= ZERO:
            self.ledger.append("paper_rejected", {"reason": decision.reason, "signal_id": decision.signal.signal_id})
            return False
        signal = decision.signal
        notional = signal.price * signal.size
        if notional > self.cash:
            self.ledger.append("paper_rejected", {"reason": "insufficient_cash", "signal_id": signal.signal_id})
            return False
        self.cash -= notional
        existing = self.positions.get(signal.market.market_id)
        if existing:
            total_qty = existing.quantity + signal.size
            total_cost = existing.cost + notional
            existing.quantity = total_qty
            existing.cost = total_cost
            existing.average_price = total_cost / total_qty
        else:
            self.positions[signal.market.market_id] = PaperPosition(
                market_id=signal.market.market_id,
                token_id=signal.market.token_id,
                quantity=signal.size,
                average_price=signal.price,
                cost=notional,
            )
        self.ledger.append("paper_fill", {"decision": asdict(decision), "cash_after": self.cash})
        return True

    def mark_to_market(self, prices: dict[str, Decimal]) -> Decimal:
        value = ZERO
        for market_id, position in self.positions.items():
            value += position.quantity * D(prices.get(market_id, position.average_price))
        return self.cash + value

    def snapshot(self) -> dict[str, Any]:
        return {
            "cash": self.cash,
            "starting_cash": self.starting_cash,
            "positions": {key: asdict(value) for key, value in self.positions.items()},
        }
