from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from risk.core import AccountSnapshot, D, IntentKind, OrderIntent, Position, RiskPolicy, ZERO
from risk.governor import MarketSnapshot as RiskMarketSnapshot
from risk.governor import RiskGovernor

from .data import PublicDataClient
from .models import Decision, Market, MarketSnapshot, Signal
from .paper import EventLedger, PaperBroker
from .simulation import MonteCarloEngine
from .strategy import StructuralScanner


@dataclass(frozen=True)
class BotConfig:
    starting_cash: Decimal = Decimal("30")
    simulation_trials: int = 10_000
    market_limit: int = 50
    min_edge: Decimal = Decimal("0.01")
    ledger_path: str = "data/events.jsonl"
    volatility_profile: str = "standard"


class TradingBot:
    """Paper-first bot pipeline. Live execution is intentionally not wired in."""

    def __init__(self, config: BotConfig | None = None, data_client: PublicDataClient | None = None):
        self.config = config or BotConfig()
        self.data_client = data_client or PublicDataClient()
        self.scanner = StructuralScanner(min_edge=self.config.min_edge)
        self.simulator = MonteCarloEngine(
            trials=self.config.simulation_trials,
            profile_name=self.config.volatility_profile,
        )
        self.policy = RiskPolicy()
        self.governor = RiskGovernor(self.policy)
        self.ledger = EventLedger(self.config.ledger_path)
        self.paper = PaperBroker(self.config.starting_cash, self.ledger)

    def discover(self) -> list[Market]:
        try:
            markets = self.data_client.list_markets(limit=self.config.market_limit)
        except Exception as exc:
            self.ledger.append("market_discovery_error", {"error": repr(exc)})
            return []
        self.ledger.append("market_discovery", {"count": len(markets)})
        return markets

    def scan(self, markets: list[Market]) -> list[Signal]:
        snapshots: list[MarketSnapshot] = []
        for market in markets:
            if not market.active or market.resolution_risk == "AMBIGUOUS":
                continue
            try:
                snapshots.append(self.data_client.snapshot(market))
            except Exception as exc:
                self.ledger.append("market_data_error", {"market_id": market.market_id, "error": repr(exc)})

        grouped: dict[str, list[MarketSnapshot]] = {}
        for snapshot in snapshots:
            grouped.setdefault(snapshot.market.event_id, []).append(snapshot)

        signals: list[Signal] = []
        for group in grouped.values():
            if len(group) == 2:
                outcomes = {snapshot.market.outcome.lower(): snapshot for snapshot in group}
                if "yes" in outcomes and "no" in outcomes:
                    signals.extend(self.scanner.scan_binary_parity(outcomes["yes"], outcomes["no"]))
            elif len(group) > 2:
                signals.extend(self.scanner.scan_multi_outcome(tuple(group)))
        self.ledger.append("signal_scan", {"markets": len(snapshots), "signals": [s.signal_id for s in signals]})
        return signals

    def evaluate(self, signal: Signal) -> Decision:
        simulation = self.simulator.evaluate(signal)
        if not simulation.stable:
            decision = Decision(signal, simulation, False, "MONTE_CARLO_UNSTABLE")
            self.ledger.append("decision", {"decision": decision})
            return decision

        account = self._account_snapshot()
        intent = OrderIntent(
            market_id=signal.market.market_id,
            event_id=signal.market.event_id,
            correlation_group=signal.market.correlation_group,
            token_id=signal.market.token_id,
            side="BUY",
            price=signal.price,
            size=signal.size,
            edge_after_costs=signal.edge_after_costs,
            fee_reserve=signal.estimated_fees,
            slippage_reserve=signal.estimated_slippage,
            residual_leg_loss=signal.residual_leg_loss,
            intent_kind=IntentKind.ENTRY,
            resolution_risk=signal.market.resolution_risk,
            market_metadata_hash=signal.market.metadata_hash,
            snapshot_metadata_hash=signal.market.metadata_hash,
            liquidity_safe_size=signal.size,
        )
        market = RiskMarketSnapshot(metadata_hash=signal.market.metadata_hash, feed_age_ms=0, book_age_ms=0)
        risk_decision = self.governor.evaluate(intent, account, market)
        approved = risk_decision.allowed and simulation.stable
        reason = "RISK_ALLOWED" if approved else ",".join(risk_decision.reason_codes)
        decision = Decision(signal, simulation, approved, reason)
        self.ledger.append("decision", {"decision": decision, "risk": risk_decision})
        return decision

    def run_once(self) -> list[Decision]:
        markets = self.discover()
        signals = self.scan(markets)
        decisions: list[Decision] = []
        for signal in signals:
            decision = self.evaluate(signal)
            decisions.append(decision)
            if decision.approved:
                self.paper.submit(decision)
        account = self._account_snapshot()
        self.ledger.append(
            "run_complete",
            {
                "decisions": decisions,
                "paper": self.paper.snapshot(),
                "risk_policy": self.policy,
                "circuit_state": account.circuit_state,
                "portfolio_equity": account.conservative_equity,
                "high_water_mark": account.high_water_mark,
            },
        )
        return decisions

    def _account_snapshot(self) -> AccountSnapshot:
        positions = tuple(
            Position(
                market_id=p.market_id,
                event_id=p.market_id,
                correlation_group=p.market_id,
                token_id=p.token_id,
                side="LONG",
                quantity=p.quantity,
                entry_cost=p.cost,
                conservative_exit_value=p.quantity * p.average_price,
                stress_exit_value=ZERO,
            )
            for p in self.paper.positions.values()
        )
        equity = self.paper.mark_to_market({})
        return AccountSnapshot(
            cash=self.paper.cash,
            positions=positions,
            high_water_mark=max(self.config.starting_cash, equity),
            reconciled=True,
        )
