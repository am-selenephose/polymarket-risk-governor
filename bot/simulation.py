from __future__ import annotations

import hashlib
import random
from decimal import Decimal

from .models import Signal, SimulationSummary, decimal_mean
from risk.core import D, ZERO


class MonteCarloEngine:
    """Scenario engine for stress testing a signal, not a price oracle.

    The model samples execution uncertainty around an observed signal. It must be
    calibrated with replay/live-shadow data before its output can affect sizing.
    """

    def __init__(self, trials: int = 10_000, seed_namespace: str = "risk-bot-v1"):
        if trials < 100:
            raise ValueError("trials must be at least 100")
        self.trials = trials
        self.seed_namespace = seed_namespace

    def evaluate(self, signal: Signal) -> SimulationSummary:
        seed_material = f"{self.seed_namespace}:{signal.signal_id}".encode()
        seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        rng = random.Random(seed)

        edge = signal.edge_after_costs
        # Uncertainty is intentionally pessimistic: the signal's own uncertainty
        # penalty is widened to account for execution variance and adverse fill.
        sigma = max(Decimal("0.002"), signal.model_uncertainty_penalty * Decimal("1.5"))
        outcomes: list[Decimal] = []
        for _ in range(self.trials):
            execution_shock = D(rng.gauss(0.0, float(sigma)))
            adverse_fill = D(rng.random()) * signal.estimated_slippage
            residual_penalty = signal.residual_leg_loss if rng.random() < Decimal("0.10") else ZERO
            outcomes.append(edge + execution_shock - adverse_fill - residual_penalty)

        ordered = sorted(outcomes)
        mean = decimal_mean(outcomes)
        p05 = self._percentile(ordered, 0.05)
        p50 = self._percentile(ordered, 0.50)
        p95 = self._percentile(ordered, 0.95)
        losses = sum(1 for outcome in outcomes if outcome < ZERO)
        loss_probability = D(losses) / D(self.trials)
        stable = mean > ZERO and p05 > -max(abs(mean), D("0.005")) and loss_probability < D("0.35")
        return SimulationSummary(
            trials=self.trials,
            mean_return=mean,
            p05_return=p05,
            p50_return=p50,
            p95_return=p95,
            loss_probability=loss_probability,
            max_loss=min(outcomes),
            max_gain=max(outcomes),
            stable=stable,
        )

    @staticmethod
    def _percentile(values: list[Decimal], fraction: float) -> Decimal:
        if not values:
            return ZERO
        index = min(len(values) - 1, max(0, int((len(values) - 1) * fraction)))
        return values[index]
