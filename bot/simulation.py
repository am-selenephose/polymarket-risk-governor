from __future__ import annotations

import hashlib
import random
from decimal import Decimal

from .models import Signal, SimulationSummary, decimal_mean
from .profiles import VolatilityProfile, get_profile
from risk.core import D, ZERO


class MonteCarloEngine:
    """Scenario engine for stress testing a signal, not a price oracle.

    The model samples execution uncertainty around an observed signal. It must be
    calibrated with replay/live-shadow data before its output can affect sizing.
    """

    def __init__(
        self,
        trials: int = 10_000,
        seed_namespace: str = "risk-bot-v1",
        profile_name: str = "standard",
    ):
        if trials < 100:
            raise ValueError("trials must be at least 100")
        self.trials = trials
        self.seed_namespace = seed_namespace
        self.profile = get_profile(profile_name)

    def evaluate(self, signal: Signal) -> SimulationSummary:
        seed_material = f"{self.seed_namespace}:{self.profile.name}:{signal.signal_id}".encode()
        seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        rng = random.Random(seed)

        edge = signal.gross_edge - signal.estimated_fees - signal.estimated_slippage
        uncertainty_penalty = signal.model_uncertainty_penalty * self.profile.uncertainty_penalty_multiplier
        sigma = max(self.profile.sigma, uncertainty_penalty)
        outcomes: list[Decimal] = []
        for _ in range(self.trials):
            execution_shock = D(rng.gauss(0.0, float(sigma)))
            adverse_fill = D(rng.random()) * signal.estimated_slippage * self.profile.adverse_fill_fraction
            residual_penalty = signal.residual_leg_loss if rng.random() < self.profile.residual_leg_probability else ZERO
            outcomes.append(edge + execution_shock - uncertainty_penalty - adverse_fill - residual_penalty)

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
            profile_name=self.profile.name,
        )

    @staticmethod
    def _percentile(values: list[Decimal], fraction: float) -> Decimal:
        if not values:
            return ZERO
        index = min(len(values) - 1, max(0, int((len(values) - 1) * fraction)))
        return values[index]
