from __future__ import annotations

import signal
import time
from dataclasses import dataclass

from .engine import TradingBot


@dataclass
class RuntimeConfig:
    interval_seconds: float = 15.0
    max_cycles: int | None = None


class PaperRuntime:
    def __init__(self, bot: TradingBot, config: RuntimeConfig | None = None):
        self.bot = bot
        self.config = config or RuntimeConfig()
        self.stop_requested = False
        self.cycles = 0

    def request_stop(self, *_args) -> None:
        self.stop_requested = True

    def run(self) -> int:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        while not self.stop_requested:
            self.bot.run_once()
            self.cycles += 1
            if self.config.max_cycles is not None and self.cycles >= self.config.max_cycles:
                break
            time.sleep(self.config.interval_seconds)
        return self.cycles
