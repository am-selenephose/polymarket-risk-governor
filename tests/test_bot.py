from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot.cli import main
from bot.models import Market, MarketSnapshot, OrderBook, Quote, Signal
from bot.paper import EventLedger, PaperBroker
from bot.simulation import MonteCarloEngine
from bot.strategy import StructuralScanner
from risk.core import D


def market(outcome: str, token: str) -> Market:
    return Market(
        market_id=f"m-{outcome}",
        event_id="event-1",
        question="Test event",
        outcome=outcome,
        token_id=token,
        resolution_source="official-source",
        correlation_group="event-1",
        metadata_hash=f"hash-{outcome}",
    )


def snapshot(outcome: str, token: str, ask: str) -> MarketSnapshot:
    m = market(outcome, token)
    return MarketSnapshot(
        market=m,
        order_book=OrderBook(
            market_id=m.market_id,
            token_id=token,
            bids=(Quote(D("0.40"), D("5")),),
            asks=(Quote(D(ask), D("5")),),
            captured_at=datetime.now(timezone.utc),
            metadata_hash=m.metadata_hash,
        ),
        midpoint=D(ask),
    )


def test_binary_scanner_finds_only_after_cost_edge():
    scanner = StructuralScanner(min_edge=D("0.01"), min_depth=D("0.10"))
    signals = scanner.scan_binary_parity(snapshot("Yes", "yes", "0.40"), snapshot("No", "no", "0.50"))
    assert len(signals) == 1
    assert signals[0].strategy == "binary_parity"
    assert signals[0].edge_after_costs > D("0.01")


def test_binary_scanner_rejects_ambiguous_resolution():
    yes = snapshot("Yes", "yes", "0.40")
    no = snapshot("No", "no", "0.50")
    no_market = Market(**{**no.market.__dict__, "resolution_risk": "AMBIGUOUS"})
    no = MarketSnapshot(no_market, no.order_book, no.midpoint)
    scanner_result = StructuralScanner(min_edge=D("0.01")).scan_binary_parity(yes, no)
    assert scanner_result == []


def test_monte_carlo_is_deterministic_and_uses_10000_trials():
    signal = Signal(
        signal_id="fixed-signal",
        strategy="test",
        market=market("Yes", "yes"),
        side="BUY",
        price=D("0.50"),
        size=D("1"),
        gross_edge=D("0.05"),
        estimated_fees=D("0"),
        estimated_slippage=D("0.001"),
        model_uncertainty_penalty=D("0.001"),
        confidence=D("0.9"),
    )
    engine = MonteCarloEngine()
    first = engine.evaluate(signal)
    second = engine.evaluate(signal)
    assert first.trials == 10_000
    assert first == second
    assert first.stable


def test_paper_broker_records_fill(tmp_path):
    ledger = EventLedger(tmp_path / "events.jsonl")
    broker = PaperBroker(D("10"), ledger)
    signal = Signal(
        signal_id="paper-signal",
        strategy="test",
        market=market("Yes", "yes"),
        side="BUY",
        price=D("0.50"),
        size=D("1"),
        gross_edge=D("0.05"),
        estimated_fees=D("0"),
        estimated_slippage=D("0"),
        model_uncertainty_penalty=D("0"),
        confidence=D("1"),
    )
    from bot.models import Decision, SimulationSummary

    decision = Decision(signal, SimulationSummary(100, D("0.05"), D("0.02"), D("0.05"), D("0.08"), D("0.1"), D("-0.01"), D("0.1"), True), True, "test")
    assert broker.submit(decision)
    assert broker.cash == D("9.50")
    assert (tmp_path / "events.jsonl").read_text().count("paper_fill") == 1


def test_live_cli_is_hard_disabled(monkeypatch):
    monkeypatch.setattr("sys.argv", ["bot", "--mode", "live"])
    with pytest.raises(SystemExit, match="Live execution is intentionally disabled"):
        main()
