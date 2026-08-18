from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class VolatilityProfile:
    name: str
    sigma: Decimal
    uncertainty_penalty_multiplier: Decimal
    adverse_fill_fraction: Decimal
    residual_leg_probability: Decimal
    description: str


PROFILES: dict[str, VolatilityProfile] = {
    "low-volatility": VolatilityProfile(
        name="low-volatility",
        sigma=Decimal("0.0025"),
        uncertainty_penalty_multiplier=Decimal("0.75"),
        adverse_fill_fraction=Decimal("0.25"),
        residual_leg_probability=Decimal("0.04"),
        description="Tighter book and lower execution-uncertainty assumption.",
    ),
    "standard": VolatilityProfile(
        name="standard",
        sigma=Decimal("0.006"),
        uncertainty_penalty_multiplier=Decimal("1.00"),
        adverse_fill_fraction=Decimal("0.50"),
        residual_leg_probability=Decimal("0.10"),
        description="Baseline paper-trading assumption before calibration.",
    ),
    "high-volatility": VolatilityProfile(
        name="high-volatility",
        sigma=Decimal("0.012"),
        uncertainty_penalty_multiplier=Decimal("1.50"),
        adverse_fill_fraction=Decimal("0.75"),
        residual_leg_probability=Decimal("0.18"),
        description="Wider fills and more execution uncertainty around fast markets.",
    ),
    "crisis": VolatilityProfile(
        name="crisis",
        sigma=Decimal("0.025"),
        uncertainty_penalty_multiplier=Decimal("2.50"),
        adverse_fill_fraction=Decimal("1.00"),
        residual_leg_probability=Decimal("0.30"),
        description="Stress scenario for dislocation, feed instability, and poor fills.",
    ),
}


def get_profile(name: str) -> VolatilityProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        choices = ", ".join(PROFILES)
        raise ValueError(f"Unknown volatility profile '{name}'. Expected one of: {choices}") from exc
