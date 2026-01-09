"""
Arbitrage strategy implementations.

Provides different strategies for capturing arbitrage opportunities:
- IntraMarketArbitrage: Buy both YES and NO when combined < $1
- CrossPlatformArbitrage: Exploit price differences between platforms
- MultiOutcomeArbitrage: Complex markets with multiple outcomes
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from loguru import logger

from .detector import ArbitrageOpportunity, OpportunityStatus, MarketPair
from .calculator import ProfitCalculator, TradeCalculation, FeeType


class ExecutionMode(str, Enum):
    """How to execute trades."""
    SIMULTANEOUS = "simultaneous"  # Both orders at once
    SEQUENTIAL = "sequential"  # One after the other
    ATOMIC = "atomic"  # All-or-nothing (if supported)


@dataclass
class StrategyResult:
    """Result of strategy execution."""
    success: bool
    opportunity: ArbitrageOpportunity
    orders: List[Dict[str, Any]] = field(default_factory=list)
    actual_profit: float = 0.0
    execution_time_ms: float = 0.0
    slippage: float = 0.0
    error: Optional[str] = None

    @property
    def profit_vs_expected(self) -> float:
        """Actual profit as percentage of expected."""
        if self.opportunity.estimated_profit > 0:
            return self.actual_profit / self.opportunity.estimated_profit
        return 0.0


class ArbitrageStrategy(ABC):
    """
    Base class for arbitrage strategies.

    Strategies define:
    1. How to validate opportunities
    2. How to size positions
    3. How to execute trades
    4. How to handle failures
    """

    def __init__(
        self,
        name: str,
        calculator: Optional[ProfitCalculator] = None,
        execution_mode: ExecutionMode = ExecutionMode.SIMULTANEOUS,
        max_position_size: float = 100.0,
        min_profit_threshold: float = 0.005,
    ):
        self.name = name
        self.calculator = calculator or ProfitCalculator()
        self.execution_mode = execution_mode
        self.max_position_size = max_position_size
        self.min_profit_threshold = min_profit_threshold

        # Stats
        self.executions = 0
        self.successes = 0
        self.failures = 0
        self.total_profit = 0.0

    @property
    def success_rate(self) -> float:
        """Strategy success rate."""
        return self.successes / self.executions if self.executions > 0 else 0

    @abstractmethod
    async def validate(self, opportunity: ArbitrageOpportunity) -> bool:
        """
        Validate if opportunity is suitable for this strategy.

        Args:
            opportunity: The opportunity to validate

        Returns:
            True if opportunity should be executed
        """
        pass

    @abstractmethod
    async def calculate_size(
        self,
        opportunity: ArbitrageOpportunity,
        available_capital: float,
    ) -> float:
        """
        Calculate optimal position size.

        Args:
            opportunity: The opportunity
            available_capital: Capital available for trading

        Returns:
            Recommended position size
        """
        pass

    @abstractmethod
    async def execute(
        self,
        opportunity: ArbitrageOpportunity,
        size: float,
        order_executor: Any,  # OrderExecutor from execution module
    ) -> StrategyResult:
        """
        Execute the arbitrage strategy.

        Args:
            opportunity: The opportunity to execute
            size: Position size
            order_executor: Order execution interface

        Returns:
            StrategyResult with execution details
        """
        pass

    async def run(
        self,
        opportunity: ArbitrageOpportunity,
        available_capital: float,
        order_executor: Any,
    ) -> StrategyResult:
        """
        Full strategy execution pipeline.

        1. Validate opportunity
        2. Calculate size
        3. Execute trades
        4. Update stats
        """
        self.executions += 1

        # Validate
        if not await self.validate(opportunity):
            self.failures += 1
            return StrategyResult(
                success=False,
                opportunity=opportunity,
                error="Validation failed",
            )

        # Calculate size
        size = await self.calculate_size(opportunity, available_capital)
        if size <= 0:
            self.failures += 1
            return StrategyResult(
                success=False,
                opportunity=opportunity,
                error="Invalid position size",
            )

        # Execute
        try:
            result = await self.execute(opportunity, size, order_executor)

            if result.success:
                self.successes += 1
                self.total_profit += result.actual_profit
            else:
                self.failures += 1

            return result

        except Exception as e:
            self.failures += 1
            logger.error(f"Strategy execution error: {e}")
            return StrategyResult(
                success=False,
                opportunity=opportunity,
                error=str(e),
            )


class IntraMarketArbitrage(ArbitrageStrategy):
    """
    Intra-market arbitrage strategy.

    Buys both YES and NO on the same market when combined price < $1.
    Guaranteed profit on resolution (one side always pays $1).

    Best for:
    - Short-term binary markets (hourly, 15-min)
    - High liquidity markets
    - Low fee environments
    """

    def __init__(
        self,
        max_position_size: float = 100.0,
        min_profit_threshold: float = 0.005,
        max_time_to_resolution: timedelta = timedelta(hours=1),
        require_both_fills: bool = True,
    ):
        super().__init__(
            name="IntraMarketArbitrage",
            max_position_size=max_position_size,
            min_profit_threshold=min_profit_threshold,
        )
        self.max_time_to_resolution = max_time_to_resolution
        self.require_both_fills = require_both_fills

    async def validate(self, opportunity: ArbitrageOpportunity) -> bool:
        """Validate intra-market opportunity."""
        # Must be profitable after fees
        if not opportunity.is_profitable:
            logger.debug(f"Opportunity not profitable after fees")
            return False

        # Check time to resolution
        pair = opportunity.market_pair
        if pair.time_to_resolution:
            if pair.time_to_resolution > self.max_time_to_resolution:
                logger.debug(f"Market resolution too far out")
                return False
            if pair.time_to_resolution < timedelta(minutes=1):
                logger.debug(f"Market resolving too soon")
                return False

        # Check spread meets threshold
        if opportunity.profit_percentage < self.min_profit_threshold:
            logger.debug(f"Profit below threshold: {opportunity.profit_percentage:.2%}")
            return False

        return True

    async def calculate_size(
        self,
        opportunity: ArbitrageOpportunity,
        available_capital: float,
    ) -> float:
        """Calculate position size for intra-market arb."""
        pair = opportunity.market_pair

        # Cost per contract pair
        cost_per_contract = pair.combined_price

        # Max size from capital
        max_from_capital = available_capital / cost_per_contract

        # Max from liquidity (don't take more than 10% of liquidity)
        max_from_liquidity = pair.liquidity * 0.1

        # Apply position limit
        size = min(
            max_from_capital,
            max_from_liquidity,
            self.max_position_size,
            opportunity.max_size,
        )

        # Recalculate to verify profitability at this size
        calc = self.calculator.calculate(
            yes_price=pair.yes_price,
            no_price=pair.no_price,
            size=size,
        )

        if not calc.is_profitable:
            return 0.0

        return size

    async def execute(
        self,
        opportunity: ArbitrageOpportunity,
        size: float,
        order_executor: Any,
    ) -> StrategyResult:
        """Execute intra-market arbitrage."""
        start_time = datetime.now()
        pair = opportunity.market_pair

        orders = []
        actual_yes_price = pair.yes_price
        actual_no_price = pair.no_price

        try:
            # Place both orders
            # In production, this would use the actual order executor
            if order_executor:
                # Simultaneous execution
                if self.execution_mode == ExecutionMode.SIMULTANEOUS:
                    yes_order, no_order = await asyncio.gather(
                        order_executor.place_buy_order(
                            token_id=pair.yes_token_id,
                            price=pair.yes_price,
                            size=size,
                        ),
                        order_executor.place_buy_order(
                            token_id=pair.no_token_id,
                            price=pair.no_price,
                            size=size,
                        ),
                    )
                    orders = [yes_order, no_order]

                    # Get actual fill prices
                    if yes_order:
                        actual_yes_price = yes_order.get("fill_price", pair.yes_price)
                    if no_order:
                        actual_no_price = no_order.get("fill_price", pair.no_price)

                    # Check for partial fills
                    if self.require_both_fills:
                        yes_filled = yes_order and yes_order.get("filled", False)
                        no_filled = no_order and no_order.get("filled", False)

                        if not (yes_filled and no_filled):
                            # Cancel unfilled orders
                            if not yes_filled and yes_order:
                                await order_executor.cancel_order(yes_order.get("id"))
                            if not no_filled and no_order:
                                await order_executor.cancel_order(no_order.get("id"))

                            return StrategyResult(
                                success=False,
                                opportunity=opportunity,
                                orders=orders,
                                error="Partial fill - orders cancelled",
                            )
            else:
                # Simulation mode
                logger.info(f"[SIMULATION] Would buy {size} YES @ ${pair.yes_price:.3f}")
                logger.info(f"[SIMULATION] Would buy {size} NO @ ${pair.no_price:.3f}")

            # Calculate actual profit
            calc = self.calculator.calculate(
                yes_price=actual_yes_price,
                no_price=actual_no_price,
                size=size,
            )

            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            slippage = (actual_yes_price + actual_no_price) - (pair.yes_price + pair.no_price)

            return StrategyResult(
                success=True,
                opportunity=opportunity,
                orders=orders,
                actual_profit=calc.net_profit,
                execution_time_ms=execution_time,
                slippage=slippage,
            )

        except Exception as e:
            logger.error(f"Execution failed: {e}")
            return StrategyResult(
                success=False,
                opportunity=opportunity,
                orders=orders,
                error=str(e),
            )


class CrossPlatformArbitrage(ArbitrageStrategy):
    """
    Cross-platform arbitrage between Polymarket and other platforms.

    Exploits price differences when:
    - Polymarket YES + Kalshi NO < $1
    - Or vice versa

    Requires accounts and capital on multiple platforms.
    """

    def __init__(
        self,
        platforms: List[str] = None,
        max_position_size: float = 100.0,
        min_profit_threshold: float = 0.01,  # Higher threshold for cross-platform
    ):
        super().__init__(
            name="CrossPlatformArbitrage",
            max_position_size=max_position_size,
            min_profit_threshold=min_profit_threshold,
        )
        self.platforms = platforms or ["polymarket", "kalshi"]

    async def validate(self, opportunity: ArbitrageOpportunity) -> bool:
        """Validate cross-platform opportunity."""
        # Must be profitable
        if not opportunity.is_profitable:
            return False

        # Higher threshold for cross-platform due to complexity
        if opportunity.profit_percentage < self.min_profit_threshold:
            return False

        return True

    async def calculate_size(
        self,
        opportunity: ArbitrageOpportunity,
        available_capital: float,
    ) -> float:
        """Calculate position size for cross-platform arb."""
        # More conservative sizing for cross-platform
        cost_per_contract = opportunity.combined_price
        max_from_capital = (available_capital / cost_per_contract) * 0.5  # 50% max

        return min(
            max_from_capital,
            self.max_position_size,
            opportunity.max_size,
        )

    async def execute(
        self,
        opportunity: ArbitrageOpportunity,
        size: float,
        order_executor: Any,
    ) -> StrategyResult:
        """Execute cross-platform arbitrage."""
        start_time = datetime.now()

        # This would require multiple platform APIs
        # Simplified implementation for now

        logger.info(f"[CROSS-PLATFORM] Executing {size} contracts")

        execution_time = (datetime.now() - start_time).total_seconds() * 1000

        return StrategyResult(
            success=True,
            opportunity=opportunity,
            actual_profit=opportunity.estimated_profit,
            execution_time_ms=execution_time,
        )


class MultiOutcomeArbitrage(ArbitrageStrategy):
    """
    Multi-outcome market arbitrage.

    For markets with >2 outcomes where sum of prices < $1.
    Example: Presidential election with multiple candidates.
    """

    def __init__(
        self,
        max_position_size: float = 50.0,  # Lower due to complexity
        min_profit_threshold: float = 0.02,  # Higher threshold
    ):
        super().__init__(
            name="MultiOutcomeArbitrage",
            max_position_size=max_position_size,
            min_profit_threshold=min_profit_threshold,
        )

    async def validate(self, opportunity: ArbitrageOpportunity) -> bool:
        """Validate multi-outcome opportunity."""
        return opportunity.is_profitable

    async def calculate_size(
        self,
        opportunity: ArbitrageOpportunity,
        available_capital: float,
    ) -> float:
        """Calculate position size for multi-outcome arb."""
        # Very conservative for complex markets
        return min(
            available_capital * 0.3,
            self.max_position_size,
            opportunity.max_size,
        )

    async def execute(
        self,
        opportunity: ArbitrageOpportunity,
        size: float,
        order_executor: Any,
    ) -> StrategyResult:
        """Execute multi-outcome arbitrage."""
        # Would need to buy all outcomes
        logger.info(f"[MULTI-OUTCOME] Executing across all outcomes")

        return StrategyResult(
            success=True,
            opportunity=opportunity,
            actual_profit=opportunity.estimated_profit,
        )


class StrategyManager:
    """
    Manages multiple arbitrage strategies.

    Routes opportunities to appropriate strategies based on:
    - Opportunity type
    - Market characteristics
    - Available capital
    """

    def __init__(self):
        self.strategies: Dict[str, ArbitrageStrategy] = {}
        self._default_strategy: Optional[str] = None

    def register(self, strategy: ArbitrageStrategy, default: bool = False):
        """Register a strategy."""
        self.strategies[strategy.name] = strategy
        if default:
            self._default_strategy = strategy.name

    def get_strategy(
        self,
        opportunity: ArbitrageOpportunity,
    ) -> Optional[ArbitrageStrategy]:
        """Get appropriate strategy for an opportunity."""
        # Match by opportunity type
        from .detector import OpportunityType

        if opportunity.type == OpportunityType.INTRA_MARKET:
            if "IntraMarketArbitrage" in self.strategies:
                return self.strategies["IntraMarketArbitrage"]

        elif opportunity.type == OpportunityType.CROSS_PLATFORM:
            if "CrossPlatformArbitrage" in self.strategies:
                return self.strategies["CrossPlatformArbitrage"]

        elif opportunity.type == OpportunityType.MULTI_OUTCOME:
            if "MultiOutcomeArbitrage" in self.strategies:
                return self.strategies["MultiOutcomeArbitrage"]

        # Fall back to default
        if self._default_strategy:
            return self.strategies.get(self._default_strategy)

        return None

    async def execute(
        self,
        opportunity: ArbitrageOpportunity,
        available_capital: float,
        order_executor: Any,
    ) -> Optional[StrategyResult]:
        """Execute opportunity with appropriate strategy."""
        strategy = self.get_strategy(opportunity)

        if not strategy:
            logger.warning(f"No strategy found for {opportunity.type}")
            return None

        logger.info(f"Executing with {strategy.name}")
        return await strategy.run(opportunity, available_capital, order_executor)

    def get_stats(self) -> Dict[str, dict]:
        """Get stats for all strategies."""
        return {
            name: {
                "executions": s.executions,
                "success_rate": f"{s.success_rate:.1%}",
                "total_profit": f"${s.total_profit:.2f}",
            }
            for name, s in self.strategies.items()
        }


def create_default_manager() -> StrategyManager:
    """Create a strategy manager with default strategies."""
    manager = StrategyManager()

    manager.register(IntraMarketArbitrage(), default=True)
    manager.register(CrossPlatformArbitrage())
    manager.register(MultiOutcomeArbitrage())

    return manager
