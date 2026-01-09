"""
Profit calculation module for arbitrage opportunities.

Handles all financial calculations including:
- Gross profit estimation
- Fee calculations (taker, maker, gas)
- Net profit after all costs
- Risk-adjusted returns
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Tuple
from enum import Enum

from loguru import logger


class FeeType(str, Enum):
    """Order fee type."""
    TAKER = "taker"
    MAKER = "maker"


@dataclass
class FeeStructure:
    """Fee structure for calculations."""
    taker_fee: float = 0.0315  # 3.15% taker fee (dynamic, can be lower)
    maker_rebate: float = 0.005  # 0.5% maker rebate
    gas_cost_usdc: float = 0.01  # Approximate gas in USDC equivalent

    def get_fee(self, fee_type: FeeType) -> float:
        """Get fee rate by type."""
        if fee_type == FeeType.TAKER:
            return self.taker_fee
        return -self.maker_rebate  # Negative = rebate


@dataclass
class TradeCalculation:
    """Result of a trade profit calculation."""
    # Input values
    yes_price: float
    no_price: float
    size: float  # Number of contracts

    # Gross values
    combined_price: float = 0.0
    gross_profit_per_contract: float = 0.0
    gross_profit_total: float = 0.0

    # Fee breakdown
    yes_fee: float = 0.0
    no_fee: float = 0.0
    total_fees: float = 0.0
    gas_cost: float = 0.0

    # Net values
    net_profit: float = 0.0
    net_profit_percentage: float = 0.0

    # Investment metrics
    total_investment: float = 0.0
    roi: float = 0.0  # Return on investment

    # Risk metrics
    max_loss: float = 0.0  # If market moves against us
    breakeven_combined: float = 0.0  # Combined price at breakeven

    @property
    def is_profitable(self) -> bool:
        """Check if trade is profitable after fees."""
        return self.net_profit > 0

    @property
    def payout_at_resolution(self) -> float:
        """Expected payout when market resolves."""
        return self.size  # Always $1 per contract

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "yes_price": self.yes_price,
            "no_price": self.no_price,
            "size": self.size,
            "combined_price": self.combined_price,
            "gross_profit": self.gross_profit_total,
            "total_fees": self.total_fees,
            "gas_cost": self.gas_cost,
            "net_profit": self.net_profit,
            "net_profit_pct": f"{self.net_profit_percentage:.2%}",
            "roi": f"{self.roi:.2%}",
            "is_profitable": self.is_profitable,
        }


class ProfitCalculator:
    """
    Calculator for arbitrage profit and loss.

    Handles the math for buying both sides of a binary market:
    - If YES + NO < $1, buy both and guaranteed profit on resolution
    - Must account for fees (up to 3.15% taker) and gas

    Example:
        calc = ProfitCalculator()
        result = calc.calculate(
            yes_price=0.48,
            no_price=0.48,
            size=100,
        )
        print(f"Net profit: ${result.net_profit:.2f}")
    """

    def __init__(
        self,
        fee_structure: Optional[FeeStructure] = None,
    ):
        self.fees = fee_structure or FeeStructure()

    def calculate(
        self,
        yes_price: float,
        no_price: float,
        size: float,
        yes_fee_type: FeeType = FeeType.TAKER,
        no_fee_type: FeeType = FeeType.TAKER,
        include_gas: bool = True,
    ) -> TradeCalculation:
        """
        Calculate profit for buying both YES and NO.

        In a binary market:
        - One side WILL pay out $1 per contract
        - Cost = (YES price + NO price) * size
        - Payout = $1 * size (guaranteed)
        - Profit = Payout - Cost - Fees

        Args:
            yes_price: Price of YES outcome (0-1)
            no_price: Price of NO outcome (0-1)
            size: Number of contracts to buy on each side
            yes_fee_type: Fee type for YES order
            no_fee_type: Fee type for NO order
            include_gas: Whether to include gas costs

        Returns:
            TradeCalculation with all profit metrics
        """
        result = TradeCalculation(
            yes_price=yes_price,
            no_price=no_price,
            size=size,
        )

        # Combined price (cost per contract pair)
        result.combined_price = yes_price + no_price

        # Gross profit calculation
        # If combined < $1, there's an arbitrage opportunity
        result.gross_profit_per_contract = 1.0 - result.combined_price
        result.gross_profit_total = result.gross_profit_per_contract * size

        # Calculate fees
        yes_fee_rate = self.fees.get_fee(yes_fee_type)
        no_fee_rate = self.fees.get_fee(no_fee_type)

        # Fees are charged on the notional value of each trade
        yes_notional = yes_price * size
        no_notional = no_price * size

        result.yes_fee = yes_notional * yes_fee_rate
        result.no_fee = no_notional * no_fee_rate
        result.total_fees = result.yes_fee + result.no_fee

        # Gas costs (two trades)
        if include_gas:
            result.gas_cost = self.fees.gas_cost_usdc * 2

        # Total investment
        result.total_investment = yes_notional + no_notional

        # Net profit
        result.net_profit = (
            result.gross_profit_total
            - result.total_fees
            - result.gas_cost
        )

        # Percentage profit
        if result.total_investment > 0:
            result.net_profit_percentage = (
                result.net_profit / result.total_investment
            )
            result.roi = result.net_profit_percentage

        # Risk metrics
        result.max_loss = result.total_fees + result.gas_cost
        result.breakeven_combined = 1.0 - (
            (result.total_fees + result.gas_cost) / size
        )

        return result

    def calculate_optimal_size(
        self,
        yes_price: float,
        no_price: float,
        available_capital: float,
        max_position: float,
        min_profit_pct: float = 0.005,
    ) -> Tuple[float, TradeCalculation]:
        """
        Calculate optimal position size for an opportunity.

        Args:
            yes_price: YES price
            no_price: NO price
            available_capital: Capital available for trading
            max_position: Maximum position size limit
            min_profit_pct: Minimum required profit percentage

        Returns:
            Tuple of (optimal_size, calculation)
        """
        combined = yes_price + no_price

        # Can't arbitrage if combined >= 1
        if combined >= 1.0:
            return 0, self.calculate(yes_price, no_price, 0)

        # Calculate how many contracts we can buy
        cost_per_contract = combined
        max_from_capital = available_capital / cost_per_contract

        # Use the smaller of capital constraint and position limit
        size = min(max_from_capital, max_position)

        # Calculate at this size
        calc = self.calculate(yes_price, no_price, size)

        # Check if meets minimum profit threshold
        if calc.net_profit_percentage < min_profit_pct:
            return 0, calc

        return size, calc

    def minimum_spread_for_profit(
        self,
        fee_type: FeeType = FeeType.TAKER,
    ) -> float:
        """
        Calculate minimum spread needed to be profitable.

        With 3.15% taker fees on both sides, need spread > ~6.3% + gas.

        Returns:
            Minimum combined price discount from $1 needed
        """
        # Fee on both sides
        fee_rate = self.fees.get_fee(fee_type)

        # Total fee impact (approximate)
        # If buying at price p, fee is p * rate
        # For combined = 0.96, total fee ≈ 0.96 * rate * 2
        # Need: 1 - combined > combined * rate * 2 + gas

        # Solving: combined < (1 - gas) / (1 + 2*rate)
        gas = self.fees.gas_cost_usdc * 2 / 100  # Per $100 investment
        min_discount = (2 * fee_rate) + gas

        return min_discount

    def simulate_price_scenarios(
        self,
        base_yes_price: float,
        base_no_price: float,
        size: float,
        price_changes: list = [-0.02, -0.01, 0, 0.01, 0.02],
    ) -> list:
        """
        Simulate profit under different price scenarios.

        Useful for understanding sensitivity to price movements.

        Args:
            base_yes_price: Starting YES price
            base_no_price: Starting NO price
            size: Position size
            price_changes: List of price changes to simulate

        Returns:
            List of (price_change, calculation) tuples
        """
        results = []

        for change in price_changes:
            # Simulate price moving against us before fill
            yes_price = base_yes_price + change
            no_price = base_no_price + change

            # Clamp to valid range
            yes_price = max(0.01, min(0.99, yes_price))
            no_price = max(0.01, min(0.99, no_price))

            calc = self.calculate(yes_price, no_price, size)
            results.append((change, calc))

        return results


class CrossPlatformCalculator(ProfitCalculator):
    """
    Calculator for cross-platform arbitrage (e.g., Polymarket vs Kalshi).

    Different platforms have different fee structures and price formats.
    """

    def __init__(
        self,
        polymarket_fees: Optional[FeeStructure] = None,
        kalshi_fees: Optional[FeeStructure] = None,
    ):
        super().__init__(polymarket_fees)
        self.kalshi_fees = kalshi_fees or FeeStructure(
            taker_fee=0.03,  # Kalshi typically 3% or less
            maker_rebate=0.0,
            gas_cost_usdc=0.0,  # No gas on Kalshi
        )

    def calculate_cross_platform(
        self,
        poly_yes_price: float,
        kalshi_no_price: float,
        size: float,
    ) -> TradeCalculation:
        """
        Calculate profit for cross-platform arbitrage.

        Buy YES on Polymarket, NO on Kalshi (or vice versa).
        One will pay out, guaranteeing profit if combined < $1.

        Args:
            poly_yes_price: Polymarket YES price
            kalshi_no_price: Kalshi NO price
            size: Number of contracts

        Returns:
            TradeCalculation
        """
        result = TradeCalculation(
            yes_price=poly_yes_price,
            no_price=kalshi_no_price,
            size=size,
        )

        result.combined_price = poly_yes_price + kalshi_no_price
        result.gross_profit_per_contract = 1.0 - result.combined_price
        result.gross_profit_total = result.gross_profit_per_contract * size

        # Different fees per platform
        poly_fee = poly_yes_price * size * self.fees.taker_fee
        kalshi_fee = kalshi_no_price * size * self.kalshi_fees.taker_fee

        result.yes_fee = poly_fee
        result.no_fee = kalshi_fee
        result.total_fees = poly_fee + kalshi_fee

        # Only Polymarket has gas
        result.gas_cost = self.fees.gas_cost_usdc

        result.total_investment = (poly_yes_price + kalshi_no_price) * size
        result.net_profit = (
            result.gross_profit_total
            - result.total_fees
            - result.gas_cost
        )

        if result.total_investment > 0:
            result.net_profit_percentage = (
                result.net_profit / result.total_investment
            )
            result.roi = result.net_profit_percentage

        return result


def main():
    """Test the calculator."""
    calc = ProfitCalculator()

    # Example: Combined price of $0.96 (4% gross margin)
    yes_price = 0.48
    no_price = 0.48

    result = calc.calculate(yes_price, no_price, size=100)

    print(f"=== Arbitrage Calculation ===")
    print(f"YES price: ${yes_price:.2f}")
    print(f"NO price: ${no_price:.2f}")
    print(f"Combined: ${result.combined_price:.2f}")
    print(f"Size: {result.size} contracts")
    print()
    print(f"Gross profit: ${result.gross_profit_total:.2f}")
    print(f"YES fee: ${result.yes_fee:.2f}")
    print(f"NO fee: ${result.no_fee:.2f}")
    print(f"Gas cost: ${result.gas_cost:.2f}")
    print(f"Total fees: ${result.total_fees:.2f}")
    print()
    print(f"Net profit: ${result.net_profit:.2f}")
    print(f"ROI: {result.roi:.2%}")
    print(f"Profitable: {result.is_profitable}")
    print()

    # Find minimum spread needed
    min_spread = calc.minimum_spread_for_profit()
    print(f"Minimum spread needed: {min_spread:.2%}")
    print(f"Maximum combined price: ${1 - min_spread:.3f}")


if __name__ == "__main__":
    main()
