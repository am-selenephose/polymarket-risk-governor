from __future__ import annotations

import argparse
from decimal import Decimal

from .engine import BotConfig, TradingBot
from .runtime import PaperRuntime, RuntimeConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket structural-edge research bot")
    parser.add_argument("--mode", choices=("paper", "live"), default="paper")
    parser.add_argument("--markets", type=int, default=50)
    parser.add_argument("--trials", type=int, default=10_000)
    parser.add_argument("--cash", type=Decimal, default=Decimal("30"))
    parser.add_argument("--ledger", default="data/events.jsonl")
    parser.add_argument(
        "--volatility-profile",
        choices=("low-volatility", "standard", "high-volatility", "crisis"),
        default="standard",
    )
    parser.add_argument("--watch", action="store_true", help="keep polling in paper mode")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--max-cycles", type=int, default=None)
    args = parser.parse_args()

    if args.mode == "live":
        raise SystemExit(
            "Live execution is intentionally disabled in this build. Complete shadow-mode acceptance, add a reviewed execution adapter, and enable it only through a separate audited release."
        )

    bot = TradingBot(
        BotConfig(
            starting_cash=args.cash,
            simulation_trials=args.trials,
            market_limit=args.markets,
            ledger_path=args.ledger,
            volatility_profile=args.volatility_profile,
        )
    )
    if args.watch:
        cycles = PaperRuntime(bot, RuntimeConfig(interval_seconds=args.interval, max_cycles=args.max_cycles)).run()
        print(f"paper runtime stopped: cycles={cycles} ledger={args.ledger}")
        return 0

    decisions = bot.run_once()
    approved = sum(1 for decision in decisions if decision.approved)
    print(f"paper run complete: decisions={len(decisions)} approved={approved} ledger={args.ledger}")
    for decision in decisions:
        print(
            decision.signal.signal_id,
            decision.signal.strategy,
            "APPROVED" if decision.approved else "REJECTED",
            decision.reason,
            f"p05={decision.simulation.p05_return}",
            f"loss_probability={decision.simulation.loss_probability}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
