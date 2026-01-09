"""Arbitrage detection and strategy modules."""

from .detector import ArbitrageDetector, ArbitrageOpportunity
from .calculator import ProfitCalculator, TradeCalculation
from .strategies import (
    ArbitrageStrategy,
    IntraMarketArbitrage,
    CrossPlatformArbitrage,
    MultiOutcomeArbitrage,
)

__all__ = [
    "ArbitrageDetector",
    "ArbitrageOpportunity",
    "ProfitCalculator",
    "TradeCalculation",
    "ArbitrageStrategy",
    "IntraMarketArbitrage",
    "CrossPlatformArbitrage",
    "MultiOutcomeArbitrage",
]
