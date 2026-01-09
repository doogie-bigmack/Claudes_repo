"""
Arbitrage opportunity detection engine.

Continuously monitors markets for arbitrage opportunities including:
- Intra-market: YES + NO < $1 on same platform
- Cross-platform: Polymarket vs Kalshi price discrepancies
- Multi-outcome: Complex markets with multiple outcomes

Uses real-time WebSocket feeds for low-latency detection.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Dict, List, Optional, Set

from loguru import logger

from .calculator import FeeStructure, ProfitCalculator, TradeCalculation


class OpportunityType(str, Enum):
    """Type of arbitrage opportunity."""

    INTRA_MARKET = "intra_market"  # YES + NO < $1 same market
    CROSS_PLATFORM = "cross_platform"  # Polymarket vs Kalshi
    MULTI_OUTCOME = "multi_outcome"  # Sum of outcomes < $1


class OpportunityStatus(str, Enum):
    """Status of an opportunity."""

    DETECTED = "detected"
    VALIDATED = "validated"
    EXECUTING = "executing"
    EXECUTED = "executed"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class MarketPair:
    """Represents a YES/NO market pair."""

    market_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_price: float = 0.0
    no_price: float = 0.0
    liquidity: float = 0.0
    end_time: Optional[datetime] = None
    last_update: datetime = field(default_factory=datetime.now)

    @property
    def combined_price(self) -> float:
        """Sum of YES and NO prices."""
        return self.yes_price + self.no_price

    @property
    def spread(self) -> float:
        """Discount from $1 (positive = arb opportunity)."""
        return 1.0 - self.combined_price

    @property
    def time_to_resolution(self) -> Optional[timedelta]:
        """Time until market resolves."""
        if self.end_time:
            return self.end_time - datetime.now()
        return None

    @property
    def is_short_term(self) -> bool:
        """Check if market resolves within 1 hour."""
        ttr = self.time_to_resolution
        return ttr is not None and ttr < timedelta(hours=1)


@dataclass
class ArbitrageOpportunity:
    """Represents a detected arbitrage opportunity."""

    id: str
    type: OpportunityType
    market_pair: MarketPair
    status: OpportunityStatus = OpportunityStatus.DETECTED

    # Pricing
    yes_price: float = 0.0
    no_price: float = 0.0
    combined_price: float = 0.0
    spread: float = 0.0  # Discount from $1

    # Profit calculations
    calculation: Optional[TradeCalculation] = None
    estimated_profit: float = 0.0
    profit_percentage: float = 0.0

    # Sizing
    recommended_size: float = 0.0
    max_size: float = 0.0  # Based on liquidity

    # Timing
    detected_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None

    # Execution
    execution_latency_ms: Optional[float] = None
    slippage: float = 0.0

    @property
    def is_valid(self) -> bool:
        """Check if opportunity is still valid."""
        if self.status in (OpportunityStatus.EXPIRED, OpportunityStatus.FAILED):
            return False
        if self.expires_at and datetime.now() > self.expires_at:
            return False
        return self.spread > 0

    @property
    def is_profitable(self) -> bool:
        """Check if profitable after fees."""
        return self.calculation is not None and self.calculation.is_profitable

    @property
    def age_seconds(self) -> float:
        """Seconds since detection."""
        return (datetime.now() - self.detected_at).total_seconds()

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "type": self.type.value,
            "market": self.market_pair.question[:50],
            "yes_price": f"${self.yes_price:.3f}",
            "no_price": f"${self.no_price:.3f}",
            "combined": f"${self.combined_price:.3f}",
            "spread": f"{self.spread:.2%}",
            "profit": f"${self.estimated_profit:.2f}",
            "profit_pct": f"{self.profit_percentage:.2%}",
            "size": self.recommended_size,
            "status": self.status.value,
            "age_s": f"{self.age_seconds:.1f}",
        }


# Callback type for opportunity notifications
OpportunityCallback = Callable[[ArbitrageOpportunity], None]


class ArbitrageDetector:
    """
    Real-time arbitrage opportunity detector.

    Monitors market prices and detects when:
    - YES + NO < $1 (intra-market arb)
    - Prices diverge across platforms
    - Multi-outcome sums < $1

    Example usage:
        detector = ArbitrageDetector(
            min_profit_threshold=0.005,
            min_liquidity=500,
        )

        detector.on_opportunity(lambda opp: print(f"Found: {opp.spread:.2%}"))

        # Add markets to monitor
        detector.add_market_pair(market_pair)

        # Update prices (called from WebSocket)
        detector.update_price("yes_token_id", 0.48)
        detector.update_price("no_token_id", 0.48)
    """

    def __init__(
        self,
        min_profit_threshold: float = 0.005,
        min_liquidity: float = 500.0,
        max_position_size: float = 100.0,
        fee_structure: Optional[FeeStructure] = None,
        opportunity_ttl_seconds: float = 30.0,
    ):
        """
        Initialize the detector.

        Args:
            min_profit_threshold: Minimum profit % to consider (after fees)
            min_liquidity: Minimum market liquidity in USDC
            max_position_size: Maximum position size per trade
            fee_structure: Fee configuration
            opportunity_ttl_seconds: How long opportunities remain valid
        """
        self.min_profit_threshold = min_profit_threshold
        self.min_liquidity = min_liquidity
        self.max_position_size = max_position_size
        self.opportunity_ttl = opportunity_ttl_seconds

        self.calculator = ProfitCalculator(fee_structure)

        # Market tracking
        self._market_pairs: Dict[str, MarketPair] = {}  # market_id -> pair
        self._token_to_market: Dict[str, str] = {}  # token_id -> market_id
        self._token_sides: Dict[str, str] = {}  # token_id -> "yes"/"no"

        # Opportunity tracking
        self._opportunities: Dict[str, ArbitrageOpportunity] = {}
        self._opportunity_history: List[ArbitrageOpportunity] = []

        # Callbacks
        self._callbacks: List[OpportunityCallback] = []

        # Stats
        self._opportunities_found = 0
        self._opportunities_profitable = 0
        self._total_potential_profit = 0.0

        # Lock for thread safety
        self._lock = asyncio.Lock()

    @property
    def market_count(self) -> int:
        """Number of markets being monitored."""
        return len(self._market_pairs)

    @property
    def opportunity_count(self) -> int:
        """Number of active opportunities."""
        return len(self._opportunities)

    @property
    def stats(self) -> dict:
        """Get detector statistics."""
        return {
            "markets_monitored": self.market_count,
            "active_opportunities": self.opportunity_count,
            "total_found": self._opportunities_found,
            "profitable_found": self._opportunities_profitable,
            "potential_profit": f"${self._total_potential_profit:.2f}",
        }

    def on_opportunity(self, callback: OpportunityCallback):
        """Register callback for new opportunities."""
        self._callbacks.append(callback)

    def add_market_pair(self, pair: MarketPair):
        """
        Add a market pair to monitor.

        Args:
            pair: MarketPair with YES and NO token IDs
        """
        self._market_pairs[pair.market_id] = pair
        self._token_to_market[pair.yes_token_id] = pair.market_id
        self._token_to_market[pair.no_token_id] = pair.market_id
        self._token_sides[pair.yes_token_id] = "yes"
        self._token_sides[pair.no_token_id] = "no"

        logger.debug(f"Added market pair: {pair.market_id}")

    def remove_market_pair(self, market_id: str):
        """Remove a market pair from monitoring."""
        if market_id in self._market_pairs:
            pair = self._market_pairs[market_id]
            del self._token_to_market[pair.yes_token_id]
            del self._token_to_market[pair.no_token_id]
            del self._token_sides[pair.yes_token_id]
            del self._token_sides[pair.no_token_id]
            del self._market_pairs[market_id]

    def update_price(self, token_id: str, price: float):
        """
        Update price for a token.

        Called when receiving price updates from WebSocket or API.

        Args:
            token_id: The token ID
            price: New price (0-1)
        """
        if token_id not in self._token_to_market:
            return

        market_id = self._token_to_market[token_id]
        pair = self._market_pairs.get(market_id)

        if not pair:
            return

        # Update the appropriate side
        side = self._token_sides.get(token_id)
        if side == "yes":
            pair.yes_price = price
        elif side == "no":
            pair.no_price = price

        pair.last_update = datetime.now()

        # Check for arbitrage
        self._check_arbitrage(pair)

    def update_prices(self, prices: Dict[str, float]):
        """
        Batch update prices.

        Args:
            prices: Dictionary of token_id -> price
        """
        for token_id, price in prices.items():
            self.update_price(token_id, price)

    def _check_arbitrage(self, pair: MarketPair):
        """Check if market pair has an arbitrage opportunity."""
        # Need both prices
        if pair.yes_price <= 0 or pair.no_price <= 0:
            return

        combined = pair.combined_price

        # Basic check: combined must be < $1
        if combined >= 1.0:
            # Remove any existing opportunity
            if pair.market_id in self._opportunities:
                self._opportunities[pair.market_id].status = OpportunityStatus.EXPIRED
                del self._opportunities[pair.market_id]
            return

        # Check liquidity
        if pair.liquidity < self.min_liquidity:
            return

        # Calculate potential profit
        size = min(self.max_position_size, pair.liquidity * 0.1)  # Max 10% of liquidity
        calc = self.calculator.calculate(
            yes_price=pair.yes_price,
            no_price=pair.no_price,
            size=size,
        )

        # Check if profitable after fees
        if calc.net_profit_percentage < self.min_profit_threshold:
            return

        # Create or update opportunity
        opp_id = f"arb_{pair.market_id}_{int(datetime.now().timestamp())}"

        opportunity = ArbitrageOpportunity(
            id=opp_id,
            type=OpportunityType.INTRA_MARKET,
            market_pair=pair,
            status=OpportunityStatus.DETECTED,
            yes_price=pair.yes_price,
            no_price=pair.no_price,
            combined_price=combined,
            spread=1.0 - combined,
            calculation=calc,
            estimated_profit=calc.net_profit,
            profit_percentage=calc.net_profit_percentage,
            recommended_size=size,
            max_size=pair.liquidity * 0.1,
            expires_at=datetime.now() + timedelta(seconds=self.opportunity_ttl),
        )

        # Track stats
        self._opportunities_found += 1
        if opportunity.is_profitable:
            self._opportunities_profitable += 1
            self._total_potential_profit += opportunity.estimated_profit

        # Store opportunity
        self._opportunities[pair.market_id] = opportunity
        self._opportunity_history.append(opportunity)

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(opportunity)
            except Exception as e:
                logger.error(f"Callback error: {e}")

        logger.info(
            f"Arbitrage detected: {pair.question[:40]}... "
            f"spread={opportunity.spread:.2%} "
            f"profit=${opportunity.estimated_profit:.2f}"
        )

    def get_opportunities(
        self,
        min_profit: Optional[float] = None,
        sort_by: str = "profit",
    ) -> List[ArbitrageOpportunity]:
        """
        Get current opportunities.

        Args:
            min_profit: Minimum profit threshold
            sort_by: Sort field ("profit", "spread", "detected_at")

        Returns:
            List of opportunities
        """
        opps = list(self._opportunities.values())

        # Filter expired
        opps = [o for o in opps if o.is_valid]

        # Filter by profit
        if min_profit is not None:
            opps = [o for o in opps if o.estimated_profit >= min_profit]

        # Sort
        if sort_by == "profit":
            opps.sort(key=lambda x: x.estimated_profit, reverse=True)
        elif sort_by == "spread":
            opps.sort(key=lambda x: x.spread, reverse=True)
        elif sort_by == "detected_at":
            opps.sort(key=lambda x: x.detected_at, reverse=True)

        return opps

    def get_best_opportunity(self) -> Optional[ArbitrageOpportunity]:
        """Get the most profitable current opportunity."""
        opps = self.get_opportunities()
        return opps[0] if opps else None

    def mark_executing(self, opportunity_id: str):
        """Mark an opportunity as being executed."""
        for opp in self._opportunities.values():
            if opp.id == opportunity_id:
                opp.status = OpportunityStatus.EXECUTING
                break

    def mark_executed(
        self,
        opportunity_id: str,
        latency_ms: Optional[float] = None,
        slippage: float = 0.0,
    ):
        """Mark an opportunity as executed."""
        for market_id, opp in list(self._opportunities.items()):
            if opp.id == opportunity_id:
                opp.status = OpportunityStatus.EXECUTED
                opp.executed_at = datetime.now()
                opp.execution_latency_ms = latency_ms
                opp.slippage = slippage
                del self._opportunities[market_id]
                break

    def mark_failed(self, opportunity_id: str, reason: str = ""):
        """Mark an opportunity as failed."""
        for market_id, opp in list(self._opportunities.items()):
            if opp.id == opportunity_id:
                opp.status = OpportunityStatus.FAILED
                logger.warning(f"Opportunity {opportunity_id} failed: {reason}")
                del self._opportunities[market_id]
                break

    def cleanup_expired(self):
        """Remove expired opportunities."""
        now = datetime.now()
        expired = [
            market_id
            for market_id, opp in self._opportunities.items()
            if opp.expires_at and now > opp.expires_at
        ]

        for market_id in expired:
            self._opportunities[market_id].status = OpportunityStatus.EXPIRED
            del self._opportunities[market_id]

        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired opportunities")

    async def run_cleanup_loop(self, interval: float = 5.0):
        """Run periodic cleanup of expired opportunities."""
        while True:
            self.cleanup_expired()
            await asyncio.sleep(interval)


class MultiMarketDetector(ArbitrageDetector):
    """
    Enhanced detector that monitors multiple related markets.

    Looks for opportunities across correlated markets, such as:
    - BTC hourly + BTC 15-minute markets
    - Related event outcomes
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._related_markets: Dict[str, Set[str]] = {}  # market_id -> related ids

    def link_markets(self, market_id1: str, market_id2: str):
        """Link two related markets for correlation analysis."""
        if market_id1 not in self._related_markets:
            self._related_markets[market_id1] = set()
        if market_id2 not in self._related_markets:
            self._related_markets[market_id2] = set()

        self._related_markets[market_id1].add(market_id2)
        self._related_markets[market_id2].add(market_id1)


def main():
    """Test the detector."""
    detector = ArbitrageDetector(
        min_profit_threshold=0.001,
        min_liquidity=100,
    )

    # Register callback
    detector.on_opportunity(lambda opp: print(f"OPPORTUNITY: {opp.to_dict()}"))

    # Add a test market
    pair = MarketPair(
        market_id="test_market",
        question="Will BTC be above $100k at 5PM UTC?",
        yes_token_id="yes_token_123",
        no_token_id="no_token_456",
        liquidity=10000,
    )
    detector.add_market_pair(pair)

    # Simulate price updates
    print("Updating YES price to 0.48...")
    detector.update_price("yes_token_123", 0.48)

    print("Updating NO price to 0.48...")
    detector.update_price("no_token_456", 0.48)

    # Check stats
    print(f"\nStats: {detector.stats}")

    # Get opportunities
    opps = detector.get_opportunities()
    print(f"Found {len(opps)} opportunities")


if __name__ == "__main__":
    main()
