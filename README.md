# Polymarket Research and Risk Bot

A paper-first Polymarket research bot inspired by social-media claims about high-frequency scanning and Monte Carlo decision systems. This repository is deliberately designed to test those ideas rather than repeat their marketing claims.

> **Important:** The screenshots supplied with the request contain self-reported profit claims, not independently verified performance evidence. This project does not promise profit, reproduce those figures, or treat “10,000 simulations” as a predictive guarantee.

## Current capabilities

The bot discovers public market metadata through the Gamma API, reads public CLOB order books, filters stale or ambiguous markets, detects mechanically defined binary parity and multi-outcome completeness relationships, evaluates execution uncertainty with a deterministic 10,000-trial Monte Carlo stress engine, passes every candidate through the deterministic risk governor, and records decisions and paper fills to an append-only JSONL ledger.

The existing risk governor enforces fixed-point Decimal accounting, worst-case exposure, open-order reservations, market and linked-event caps, stale-feed and metadata-hash checks, daily and rolling loss limits, drawdown gates, residual-leg controls, and fail-closed circuit states. The AI or research layer cannot override it.

## Run a paper scan

```bash
python3 -m bot.cli --mode paper --markets 50 --trials 10000 --ledger data/events.jsonl
```

The command uses public read-only endpoints. Network availability, API schemas, rate limits, and market data freshness can change. The bot records API failures rather than treating missing data as a trade. For continuous paper polling, use `python3 -m bot.cli --mode paper --watch --interval 15 --max-cycles 10`; omit `--max-cycles` only when you intentionally want a long-running process.

When the public endpoints were unreachable in this environment, the runner returned zero decisions and wrote a `market_discovery_error` event to the ledger. That is deliberate fail-closed behavior.

## Run tests

```bash
python3 -m pytest -q
```

The repository currently has an offline test suite covering structural signals, deterministic 10,000-trial simulation, paper fills, stale/ambiguous data gates, drawdown controls, reservation limits, circuit breakers, and the live-mode hard stop.

## Live trading status

Live execution is **disabled**. `python3 -m bot.cli --mode live` exits intentionally. The repository includes only a fail-closed execution interface; it does not store private keys, create authenticated clients, sign orders, submit orders, or move funds.

Before enabling any live adapter, require a separate reviewed release with authoritative balance and position reconciliation, user-stream recovery, idempotent cancellation, key isolation, explicit post-only/taker policy, partial-fill hedging, settlement monitoring, manual halt recovery, and shadow-mode evidence. The initial safe path is $0 paper mode, then live shadow mode, and only later a separately capped experiment if after-cost expectancy survives pessimistic replay.

## Architecture

```text
Gamma market discovery ─┐
                         ├─> canonical market snapshots ─> structural scanner
CLOB order books ────────┘                                      │
                                                               v
                                                    Monte Carlo stress filter
                                                               │
                                                               v
                                                       deterministic governor
                                                               │
                                            paper broker + append-only event ledger
                                                               │
                                                       metrics / post-mortems
```

## Sources

The public adapter and state assumptions follow the official Polymarket documentation for [API](https://docs.polymarket.com/getting-started/api), [Real-Time Data](https://docs.polymarket.com/market-data/realtime-data), [Order Lifecycle](https://docs.polymarket.com/concepts/order-lifecycle), [Fees](https://docs.polymarket.com/trading/fees), and [Negative Risk Markets](https://docs.polymarket.com/concepts/negative-risk).
