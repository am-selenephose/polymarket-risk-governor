# Polymarket Risk Governor

A deterministic, fail-closed risk-management module for a Polymarket trading bot. This repository contains the safety layer designed for the proposed $30 paper/shadow/live-experiment workflow.

## What is implemented

The module provides fixed-point Decimal accounting, versioned policy limits, worst-case position and open-order reservations, market/event concentration checks, stale-data and metadata-hash gates, daily and rolling loss gates, drawdown checks, reduce-only behavior, and a circuit breaker with `NORMAL`, `CAUTION`, `PAUSE_NEW`, `UNWIND_ONLY`, and `HALTED` states.

The risk governor **does not connect to Polymarket, sign orders, submit orders, hold private keys, or move funds**. It is intentionally isolated from execution. An execution adapter must call `RiskGovernor.evaluate()` before signing, then verify that the signed size and price exactly match the approved decision.

## Safety defaults

The initial policy uses a $10 live-experiment cap inside a larger $30 account, a $1 maximum order notional, a $2 market-loss limit, a $3 linked-event limit, a $0.50 residual-leg limit, a $2 cash safety buffer, and a $2 maximum drawdown. It is designed for paper/shadow mode and a tiny controlled experiment, not guaranteed returns.

## Run tests

```bash
python3 -m pytest -q
```

No live trading code is included. Before connecting an execution adapter, add authoritative account reconciliation, user-stream recovery, idempotent cancellation, key isolation, and a manual recovery gate for halted states.

## Integration contract

1. Build an `AccountSnapshot` from authoritative balances, positions, and open orders.
2. Build a `MarketSnapshot` from the current book and canonical market metadata hash.
3. Create an `OrderIntent` containing all-in fee/slippage reserves and residual-leg worst-case loss.
4. Call `RiskGovernor.evaluate()`.
5. Sign and submit only if `decision.allowed` is true, using exactly `decision.approved_size`.
6. Append the decision and subsequent order/trade events to an immutable ledger.
7. On reconnect, unknown fill, balance mismatch, or security anomaly, stop new signing and reconcile before resuming.

## Sources

The state model follows the official Polymarket documentation for [Order Lifecycle](https://docs.polymarket.com/concepts/order-lifecycle), [Fees](https://docs.polymarket.com/trading/fees), [Real-Time Order Updates](https://docs.polymarket.com/trading/realtime-order-updates), and [Negative Risk Markets](https://docs.polymarket.com/concepts/negative-risk).
