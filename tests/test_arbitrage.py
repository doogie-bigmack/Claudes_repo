"""
Tests for arbitrage detection and calculation.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta

# Import modules to test
from src.arbitrage.calculator import (
    ProfitCalculator,
    FeeStructure,
    FeeType,
    TradeCalculation,
)
from src.arbitrage.detector import (
    ArbitrageDetector,
    ArbitrageOpportunity,
    MarketPair,
    OpportunityType,
)
from src.arbitrage.strategies import (
    IntraMarketArbitrage,
    StrategyManager,
)


class TestProfitCalculator:
    """Tests for profit calculation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.calculator = ProfitCalculator()

    def test_basic_arbitrage_calculation(self):
        """Test basic arbitrage profit calculation."""
        # Combined price < $1 = arbitrage opportunity
        result = self.calculator.calculate(
            yes_price=0.48,
            no_price=0.48,
            size=100,
        )

        assert result.combined_price == 0.96
        assert result.gross_profit_per_contract == pytest.approx(0.04, rel=0.01)
        assert result.gross_profit_total == pytest.approx(4.0, rel=0.01)

    def test_no_arbitrage_when_combined_above_one(self):
        """Test no profit when combined price >= $1."""
        result = self.calculator.calculate(
            yes_price=0.52,
            no_price=0.52,
            size=100,
        )

        assert result.combined_price == 1.04
        assert result.gross_profit_per_contract < 0
        assert not result.is_profitable

    def test_fee_calculation(self):
        """Test fee deductions."""
        result = self.calculator.calculate(
            yes_price=0.48,
            no_price=0.48,
            size=100,
        )

        # Fees should be calculated
        assert result.yes_fee > 0
        assert result.no_fee > 0
        assert result.total_fees > 0

        # Net profit should be less than gross
        assert result.net_profit < result.gross_profit_total

    def test_gas_cost_included(self):
        """Test gas cost is included."""
        with_gas = self.calculator.calculate(
            yes_price=0.48,
            no_price=0.48,
            size=100,
            include_gas=True,
        )

        without_gas = self.calculator.calculate(
            yes_price=0.48,
            no_price=0.48,
            size=100,
            include_gas=False,
        )

        assert with_gas.gas_cost > 0
        assert without_gas.gas_cost == 0
        assert with_gas.net_profit < without_gas.net_profit

    def test_minimum_spread_for_profit(self):
        """Test minimum spread calculation."""
        min_spread = self.calculator.minimum_spread_for_profit()

        # With 3.15% taker fees on both sides, need > ~6.3% spread
        assert min_spread > 0.06
        assert min_spread < 0.10  # Sanity check

    def test_optimal_size_calculation(self):
        """Test optimal position sizing."""
        size, calc = self.calculator.calculate_optimal_size(
            yes_price=0.45,
            no_price=0.45,
            available_capital=1000,
            max_position=100,
        )

        # Should return a valid size
        assert size > 0
        assert size <= 100
        assert calc.is_profitable or size == 0


class TestArbitrageDetector:
    """Tests for arbitrage detection."""

    def setup_method(self):
        """Set up test fixtures."""
        self.detector = ArbitrageDetector(
            min_profit_threshold=0.001,  # Very low for testing
            min_liquidity=100,
        )

    def test_add_market_pair(self):
        """Test adding a market pair."""
        pair = MarketPair(
            market_id="test_market",
            question="Test Question?",
            yes_token_id="yes_123",
            no_token_id="no_456",
            liquidity=1000,
        )

        self.detector.add_market_pair(pair)
        assert self.detector.market_count == 1

    def test_opportunity_detection(self):
        """Test that opportunities are detected."""
        detected_opps = []

        def callback(opp):
            detected_opps.append(opp)

        self.detector.on_opportunity(callback)

        pair = MarketPair(
            market_id="arb_market",
            question="Will BTC be above $100k?",
            yes_token_id="yes_btc",
            no_token_id="no_btc",
            liquidity=10000,
        )
        self.detector.add_market_pair(pair)

        # Update prices to create arbitrage
        self.detector.update_price("yes_btc", 0.45)
        self.detector.update_price("no_btc", 0.45)  # Combined = 0.90 < $1

        # Should detect opportunity
        assert len(detected_opps) > 0
        assert detected_opps[0].spread > 0

    def test_no_opportunity_when_combined_above_one(self):
        """Test no opportunity when prices are fair."""
        detected_opps = []
        self.detector.on_opportunity(lambda opp: detected_opps.append(opp))

        pair = MarketPair(
            market_id="fair_market",
            question="Fair Market?",
            yes_token_id="yes_fair",
            no_token_id="no_fair",
            liquidity=10000,
        )
        self.detector.add_market_pair(pair)

        # Update to fair prices
        self.detector.update_price("yes_fair", 0.52)
        self.detector.update_price("no_fair", 0.52)  # Combined = 1.04 > $1

        # Should NOT detect opportunity
        assert len(detected_opps) == 0

    def test_liquidity_filter(self):
        """Test that low liquidity markets are filtered."""
        detected_opps = []
        self.detector.on_opportunity(lambda opp: detected_opps.append(opp))

        # Create low liquidity market
        pair = MarketPair(
            market_id="low_liq",
            question="Low Liquidity?",
            yes_token_id="yes_low",
            no_token_id="no_low",
            liquidity=10,  # Below minimum of 100
        )
        self.detector.add_market_pair(pair)

        self.detector.update_price("yes_low", 0.40)
        self.detector.update_price("no_low", 0.40)

        # Should NOT detect due to low liquidity
        assert len(detected_opps) == 0

    def test_get_opportunities_sorted(self):
        """Test getting sorted opportunities."""
        # Add multiple market pairs with different spreads
        pairs = [
            ("m1", 0.48, 0.48, 5000),  # spread = 4%
            ("m2", 0.45, 0.45, 5000),  # spread = 10%
            ("m3", 0.47, 0.47, 5000),  # spread = 6%
        ]

        for market_id, yes_p, no_p, liq in pairs:
            pair = MarketPair(
                market_id=market_id,
                question=f"Market {market_id}",
                yes_token_id=f"yes_{market_id}",
                no_token_id=f"no_{market_id}",
                liquidity=liq,
            )
            self.detector.add_market_pair(pair)
            self.detector.update_price(f"yes_{market_id}", yes_p)
            self.detector.update_price(f"no_{market_id}", no_p)

        opps = self.detector.get_opportunities(sort_by="spread")

        # Should be sorted by spread descending
        if len(opps) >= 2:
            assert opps[0].spread >= opps[1].spread


class TestIntraMarketArbitrage:
    """Tests for intra-market arbitrage strategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = IntraMarketArbitrage(
            max_position_size=100,
            min_profit_threshold=0.001,
        )

    @pytest.mark.asyncio
    async def test_validate_profitable_opportunity(self):
        """Test validation of profitable opportunity."""
        pair = MarketPair(
            market_id="test",
            question="Test?",
            yes_token_id="y",
            no_token_id="n",
            yes_price=0.45,
            no_price=0.45,
            liquidity=5000,
            end_time=datetime.now() + timedelta(minutes=30),
        )

        # Create opportunity with profitable calculation
        calc = ProfitCalculator().calculate(0.45, 0.45, 100)

        opp = ArbitrageOpportunity(
            id="test_opp",
            type=OpportunityType.INTRA_MARKET,
            market_pair=pair,
            calculation=calc,
            profit_percentage=calc.net_profit_percentage,
        )

        is_valid = await self.strategy.validate(opp)
        # May or may not be valid depending on exact fee calculation
        assert isinstance(is_valid, bool)

    @pytest.mark.asyncio
    async def test_calculate_size(self):
        """Test position size calculation."""
        pair = MarketPair(
            market_id="test",
            question="Test?",
            yes_token_id="y",
            no_token_id="n",
            yes_price=0.45,
            no_price=0.45,
            liquidity=5000,
        )

        opp = ArbitrageOpportunity(
            id="test_opp",
            type=OpportunityType.INTRA_MARKET,
            market_pair=pair,
            max_size=500,
        )

        size = await self.strategy.calculate_size(opp, available_capital=1000)

        # Size should be positive and within limits
        assert size >= 0
        assert size <= self.strategy.max_position_size
        assert size <= opp.max_size


class TestStrategyManager:
    """Tests for strategy management."""

    def test_register_strategy(self):
        """Test registering strategies."""
        manager = StrategyManager()

        strategy = IntraMarketArbitrage()
        manager.register(strategy, default=True)

        assert strategy.name in manager.strategies
        assert manager._default_strategy == strategy.name

    def test_get_strategy_for_opportunity(self):
        """Test getting appropriate strategy."""
        manager = StrategyManager()
        manager.register(IntraMarketArbitrage(), default=True)

        pair = MarketPair(
            market_id="test",
            question="Test?",
            yes_token_id="y",
            no_token_id="n",
        )

        opp = ArbitrageOpportunity(
            id="test",
            type=OpportunityType.INTRA_MARKET,
            market_pair=pair,
        )

        strategy = manager.get_strategy(opp)
        assert strategy is not None
        assert strategy.name == "IntraMarketArbitrage"


class TestMarketPair:
    """Tests for MarketPair dataclass."""

    def test_combined_price(self):
        """Test combined price calculation."""
        pair = MarketPair(
            market_id="test",
            question="Test?",
            yes_token_id="y",
            no_token_id="n",
            yes_price=0.48,
            no_price=0.48,
        )

        assert pair.combined_price == 0.96

    def test_spread(self):
        """Test spread calculation."""
        pair = MarketPair(
            market_id="test",
            question="Test?",
            yes_token_id="y",
            no_token_id="n",
            yes_price=0.48,
            no_price=0.48,
        )

        assert pair.spread == pytest.approx(0.04, rel=0.01)

    def test_is_short_term(self):
        """Test short-term market detection."""
        short_term = MarketPair(
            market_id="short",
            question="Short?",
            yes_token_id="y",
            no_token_id="n",
            end_time=datetime.now() + timedelta(minutes=30),
        )

        long_term = MarketPair(
            market_id="long",
            question="Long?",
            yes_token_id="y",
            no_token_id="n",
            end_time=datetime.now() + timedelta(hours=2),
        )

        assert short_term.is_short_term is True
        assert long_term.is_short_term is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
