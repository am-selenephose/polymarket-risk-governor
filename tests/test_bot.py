from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from bot.cli import main
from bot.models import Market, MarketSnapshot, OrderBook, Quote, Signal
from bot.paper import EventLedger, PaperBroker
from bot.profiles import PROFILES
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


def multi_outcomes(*asks: str) -> tuple[MarketSnapshot, ...]:
    return tuple(snapshot(f"Outcome {index}", f"outcome-{index}", ask) for index, ask in enumerate(asks, start=1))


def test_multi_outcome_completeness_edge_detected_correctly():
    scanner = StructuralScanner(min_edge=D("0.01"), min_depth=D("0.10"))
    signals = scanner.scan_multi_outcome(multi_outcomes("0.30", "0.30", "0.30"))
    assert len(signals) == 1
    assert signals[0].strategy == "multi_outcome_completeness"
    assert signals[0].gross_edge == D("0.10")
    assert signals[0].edge_after_costs > D("0.01")


def test_multi_outcome_completeness_edge_below_minimum_rejected():
    scanner = StructuralScanner(min_edge=D("0.01"), min_depth=D("0.10"))
    assert scanner.scan_multi_outcome(multi_outcomes("0.33", "0.34", "0.33")) == []


def test_multi_outcome_completeness_stale_book_rejected():
    scanner = StructuralScanner(min_edge=D("0.01"), max_book_age_ms=3_000)
    outcomes = list(multi_outcomes("0.30", "0.30", "0.30"))
    outcomes[1] = replace(outcomes[1], captured_at=datetime.now(timezone.utc) - timedelta(milliseconds=3_001))
    assert scanner.scan_multi_outcome(tuple(outcomes)) == []


def test_multi_outcome_completeness_ambiguous_resolution_rejected():
    scanner = StructuralScanner(min_edge=D("0.01"))
    outcomes = list(multi_outcomes("0.30", "0.30", "0.30"))
    outcomes[1] = replace(outcomes[1], market=replace(outcomes[1].market, resolution_risk="AMBIGUOUS"))
    assert scanner.scan_multi_outcome(tuple(outcomes)) == []


def test_multi_outcome_completeness_augmented_neg_risk_rejected():
    scanner = StructuralScanner(min_edge=D("0.01"))
    outcomes = list(multi_outcomes("0.30", "0.30", "0.30"))
    outcomes[2] = replace(outcomes[2], market=replace(outcomes[2].market, augmented_neg_risk=True))
    assert scanner.scan_multi_outcome(tuple(outcomes)) == []


def test_multi_outcome_completeness_mismatched_event_ids_rejected():
    scanner = StructuralScanner(min_edge=D("0.01"))
    outcomes = list(multi_outcomes("0.30", "0.30", "0.30"))
    outcomes[2] = replace(outcomes[2], market=replace(outcomes[2].market, event_id="event-2"))
    assert scanner.scan_multi_outcome(tuple(outcomes)) == []


def test_multi_outcome_completeness_mismatched_resolution_sources_rejected():
    scanner = StructuralScanner(min_edge=D("0.01"))
    outcomes = list(multi_outcomes("0.30", "0.30", "0.30"))
    outcomes[2] = replace(outcomes[2], market=replace(outcomes[2].market, resolution_source="different-source"))
    assert scanner.scan_multi_outcome(tuple(outcomes)) == []


def test_multi_outcome_completeness_insufficient_depth_rejected():
    scanner = StructuralScanner(min_edge=D("0.01"), min_depth=D("0.10"))
    outcomes = list(multi_outcomes("0.30", "0.30", "0.30"))
    shallow_book = replace(outcomes[0].order_book, asks=(Quote(D("0.30"), D("0.05")),))
    outcomes[0] = replace(outcomes[0], order_book=shallow_book)
    assert scanner.scan_multi_outcome(tuple(outcomes)) == []


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


def test_named_volatility_profiles_change_simulation_stress_assumptions():
    assert set(PROFILES) == {"low-volatility", "standard", "high-volatility", "crisis"}
    signal = Signal(
        signal_id="profile-signal",
        strategy="test",
        market=market("Yes", "yes"),
        side="BUY",
        price=D("0.50"),
        size=D("1"),
        gross_edge=D("0.05"),
        estimated_fees=D("0"),
        estimated_slippage=D("0.004"),
        model_uncertainty_penalty=D("0.003"),
        confidence=D("0.9"),
        residual_leg_loss=D("0.02"),
    )
    low = MonteCarloEngine(trials=100, profile_name="low-volatility").evaluate(signal)
    crisis = MonteCarloEngine(trials=100, profile_name="crisis").evaluate(signal)
    assert low.profile_name == "low-volatility"
    assert crisis.profile_name == "crisis"
    assert crisis.p05_return < low.p05_return


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


def test_event_ledger_serializes_dataclass_decision_payload(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    ledger = EventLedger(ledger_path)
    signal = Signal(
        signal_id="ledger-signal",
        strategy="binary_parity",
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

    decision = Decision(signal, SimulationSummary(100, D("0.05"), D("0.02"), D("0.05"), D("0.08"), D("0.1"), D("-0.01"), D("0.1"), True), True, "RISK_ALLOWED")
    ledger.append("decision", {"decision": decision})
    record = json.loads(ledger_path.read_text())
    assert record["payload"]["decision"]["signal"]["signal_id"] == "ledger-signal"


def test_live_cli_is_hard_disabled(monkeypatch):
    monkeypatch.setattr("sys.argv", ["bot", "--mode", "live"])
    with pytest.raises(SystemExit, match="Live execution is intentionally disabled"):
        main()
