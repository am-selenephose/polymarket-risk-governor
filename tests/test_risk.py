from decimal import Decimal

from risk import (
    AccountSnapshot,
    CircuitBreaker,
    CircuitState,
    D,
    DecisionStatus,
    IntentKind,
    MarketSnapshot,
    OpenOrder,
    OrderIntent,
    Position,
    RiskGovernor,
    RiskPolicy,
)


POLICY = RiskPolicy()
MARKET = MarketSnapshot(metadata_hash="m1", feed_age_ms=100, book_age_ms=100)


def intent(**overrides):
    values = dict(
        market_id="market-a",
        event_id="event-a",
        correlation_group="event-a",
        token_id="yes-a",
        side="BUY",
        price=D("0.50"),
        size=D("1"),
        edge_after_costs=D("0.02"),
        fee_reserve=D("0.01"),
        slippage_reserve=D("0.01"),
        residual_leg_loss=D("0"),
        market_metadata_hash="m1",
        snapshot_metadata_hash="m1",
        liquidity_safe_size=D("1"),
    )
    values.update(overrides)
    return OrderIntent(**values)


def account(**overrides):
    values = dict(cash=D("10"), high_water_mark=D("10"))
    values.update(overrides)
    return AccountSnapshot(**values)


def test_allows_small_entry_under_limits():
    decision = RiskGovernor(POLICY).evaluate(intent(), account(), MARKET)
    assert decision.status is DecisionStatus.ALLOW
    assert decision.approved_size == D("1")


def test_clips_entry_to_order_cap():
    decision = RiskGovernor(POLICY).evaluate(intent(size=D("10")), account(), MARKET)
    assert decision.status is DecisionStatus.REJECT
    assert "ORDER_NOTIONAL_LIMIT" in decision.reason_codes


def test_rejects_stale_feed():
    decision = RiskGovernor(POLICY).evaluate(intent(), account(), MarketSnapshot("m1", 3_001, book_age_ms=100))
    assert decision.status is DecisionStatus.REJECT
    assert decision.reason_codes == ("STALE_FEED",)


def test_rejects_metadata_change_before_signing():
    decision = RiskGovernor(POLICY).evaluate(intent(), account(), MarketSnapshot("m2", 100, book_age_ms=100))
    assert decision.status is DecisionStatus.REJECT
    assert decision.reason_codes == ("METADATA_HASH_CHANGED",)


def test_rejects_ambiguous_resolution():
    decision = RiskGovernor(POLICY).evaluate(intent(resolution_risk="AMBIGUOUS"), account(), MARKET)
    assert decision.status is DecisionStatus.REJECT
    assert decision.reason_codes == ("AMBIGUOUS_RESOLUTION",)


def test_rejects_when_safety_cash_buffer_would_be_breached():
    decision = RiskGovernor(POLICY).evaluate(intent(size=D("2")), account(cash=D("2.40")), MARKET)
    assert decision.status is DecisionStatus.REJECT
    assert "SAFETY_CASH_BUFFER" in decision.reason_codes


def test_rejects_daily_loss_and_allows_reduce_only():
    losing_account = account(realized_pnl_today=D("-0.50"))
    entry = RiskGovernor(POLICY).evaluate(intent(), losing_account, MARKET)
    assert entry.status is DecisionStatus.REJECT
    assert "HARD_DAILY_LOSS" in entry.reason_codes

    reduction = RiskGovernor(POLICY).evaluate(
        intent(intent_kind=IntentKind.REDUCE_ONLY, edge_after_costs=D("-1")),
        losing_account,
        MARKET,
    )
    assert reduction.allowed


def test_rejects_drawdown_breach():
    drawdown_account = account(cash=D("7.99"), high_water_mark=D("10"))
    decision = RiskGovernor(POLICY).evaluate(intent(), drawdown_account, MARKET)
    assert decision.status is DecisionStatus.REJECT
    assert "MAX_DRAWDOWN" in decision.reason_codes


def test_rejects_unreconciled_account():
    decision = RiskGovernor(POLICY).evaluate(intent(), account(reconciled=False), MARKET)
    assert decision.status is DecisionStatus.REJECT
    assert decision.reason_codes == ("ACCOUNT_NOT_RECONCILED",)


def test_open_order_reservation_reduces_free_cash():
    existing = OpenOrder(
        order_id="o1",
        market_id="market-b",
        event_id="event-b",
        correlation_group="event-b",
        token_id="yes-b",
        side="BUY",
        price=D("0.50"),
        remaining_size=D("16"),
    )
    decision = RiskGovernor(POLICY).evaluate(intent(), account(open_orders=(existing,)), MARKET)
    assert decision.status is DecisionStatus.REJECT
    assert "SAFETY_CASH_BUFFER" in decision.reason_codes


def test_residual_leg_limit_is_hard():
    decision = RiskGovernor(POLICY).evaluate(intent(residual_leg_loss=D("0.51")), account(), MARKET)
    assert decision.status is DecisionStatus.REJECT
    assert decision.reason_codes == ("RESIDUAL_LEG_LIMIT",)


def test_circuit_breaker_fails_closed_for_unknown_fill():
    breaker = CircuitBreaker()
    assert breaker.trigger("UNKNOWN_FILL") is CircuitState.HALTED
    assert breaker.clear_after_reconciliation(manual_reset=False) is CircuitState.HALTED
    assert breaker.clear_after_reconciliation(manual_reset=True) is CircuitState.CAUTION


def test_circuit_breaker_enters_unwind_only_on_drawdown():
    breaker = CircuitBreaker()
    assert breaker.trigger("DRAWDOWN_BREACH") is CircuitState.UNWIND_ONLY
    decision = RiskGovernor(POLICY).evaluate(intent(), account(circuit_state=CircuitState.UNWIND_ONLY), MARKET)
    assert decision.status is DecisionStatus.REJECT
