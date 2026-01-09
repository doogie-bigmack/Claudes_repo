"""
Risk management system for arbitrage trading.

Implements various risk controls:
- Position size limits
- Daily loss limits
- Trade frequency limits
- Exposure management
- Circuit breakers
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional

from loguru import logger


class RiskLevel(str, Enum):
    """Risk level classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskCheckResult(str, Enum):
    """Result of a risk check."""

    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"


@dataclass
class RiskLimits:
    """Risk limit configuration."""

    # Position limits
    max_position_size: float = 100.0  # Max per trade
    max_total_exposure: float = 1000.0  # Total across all positions
    max_single_market_exposure: float = 500.0  # Per market

    # Loss limits
    max_daily_loss: float = 50.0  # Absolute USD
    max_daily_loss_pct: float = 0.05  # 5% of capital
    max_trade_loss: float = 20.0  # Max loss per trade

    # Frequency limits
    max_trades_per_minute: int = 10
    max_trades_per_hour: int = 100
    max_trades_per_day: int = 500

    # Cooldown
    min_trade_interval_seconds: float = 5.0
    cooldown_after_loss_seconds: float = 60.0

    # Circuit breakers
    consecutive_loss_limit: int = 5
    drawdown_halt_pct: float = 0.10  # Halt at 10% drawdown


@dataclass
class RiskCheck:
    """Result of a risk check."""

    name: str
    result: RiskCheckResult
    level: RiskLevel
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def passed(self) -> bool:
        return self.result == RiskCheckResult.PASSED

    @property
    def blocked(self) -> bool:
        return self.result == RiskCheckResult.BLOCKED


@dataclass
class TradeRecord:
    """Record of a completed trade."""

    timestamp: datetime
    token_id: str
    side: str
    size: float
    price: float
    pnl: float
    fees: float


@dataclass
class RiskState:
    """Current risk state."""

    # Capital tracking
    starting_capital: float = 0.0
    current_capital: float = 0.0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0

    # Position tracking
    total_exposure: float = 0.0
    market_exposures: Dict[str, float] = field(default_factory=dict)
    open_position_count: int = 0

    # Trade tracking
    trades_today: int = 0
    trades_this_hour: int = 0
    trades_this_minute: int = 0
    consecutive_losses: int = 0
    last_trade_time: Optional[datetime] = None

    # Status
    is_halted: bool = False
    halt_reason: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.LOW


class RiskManager:
    """
    Risk management controller.

    Monitors trading activity and enforces risk limits.

    Example:
        risk = RiskManager(limits, starting_capital=1000)

        # Before each trade
        check = risk.check_trade(token_id, size, expected_cost)
        if check.blocked:
            print(f"Trade blocked: {check.message}")
            return

        # After trade execution
        risk.record_trade(trade_record)

        # Check if trading should continue
        if risk.state.is_halted:
            print(f"Trading halted: {risk.state.halt_reason}")
    """

    def __init__(
        self,
        limits: Optional[RiskLimits] = None,
        starting_capital: float = 1000.0,
    ):
        self.limits = limits or RiskLimits()
        self.state = RiskState(
            starting_capital=starting_capital,
            current_capital=starting_capital,
        )

        # Trade history for rate limiting
        self._trade_times: deque = deque(maxlen=1000)
        self._recent_pnls: deque = deque(maxlen=100)

        # Daily reset tracking
        self._day_start = datetime.now().replace(hour=0, minute=0, second=0)

        logger.info(f"Risk manager initialized with ${starting_capital:.2f} capital")

    @property
    def is_trading_allowed(self) -> bool:
        """Check if trading is currently allowed."""
        return not self.state.is_halted

    @property
    def available_capital(self) -> float:
        """Capital available for new trades."""
        return max(0, self.state.current_capital - self.state.total_exposure)

    @property
    def drawdown(self) -> float:
        """Current drawdown from starting capital."""
        if self.state.starting_capital <= 0:
            return 0
        return (
            self.state.starting_capital - self.state.current_capital
        ) / self.state.starting_capital

    def _reset_daily_counters(self):
        """Reset daily counters if new day."""
        now = datetime.now()
        day_start = now.replace(hour=0, minute=0, second=0)

        if day_start > self._day_start:
            self.state.trades_today = 0
            self.state.daily_pnl = 0.0
            self._day_start = day_start
            logger.info("Daily counters reset")

    def _update_rate_counters(self):
        """Update trade rate counters."""
        now = datetime.now()

        # Clean old entries
        minute_ago = now - timedelta(minutes=1)
        hour_ago = now - timedelta(hours=1)

        self.state.trades_this_minute = sum(
            1 for t in self._trade_times if t > minute_ago
        )
        self.state.trades_this_hour = sum(1 for t in self._trade_times if t > hour_ago)

    def check_trade(
        self,
        token_id: str,
        size: float,
        expected_cost: float,
        market_id: Optional[str] = None,
    ) -> RiskCheck:
        """
        Check if a trade passes risk controls.

        Args:
            token_id: Token being traded
            size: Trade size
            expected_cost: Expected trade cost in USDC
            market_id: Market ID for exposure tracking

        Returns:
            RiskCheck with pass/fail status
        """
        self._reset_daily_counters()
        self._update_rate_counters()

        # Check 1: Trading halted
        if self.state.is_halted:
            return RiskCheck(
                name="halt_check",
                result=RiskCheckResult.BLOCKED,
                level=RiskLevel.CRITICAL,
                message=f"Trading halted: {self.state.halt_reason}",
            )

        # Check 2: Position size limit
        if size > self.limits.max_position_size:
            return RiskCheck(
                name="position_size",
                result=RiskCheckResult.BLOCKED,
                level=RiskLevel.HIGH,
                message=f"Size {size} exceeds max {self.limits.max_position_size}",
                details={"size": size, "max": self.limits.max_position_size},
            )

        # Check 3: Available capital
        if expected_cost > self.available_capital:
            return RiskCheck(
                name="capital_check",
                result=RiskCheckResult.BLOCKED,
                level=RiskLevel.HIGH,
                message=f"Insufficient capital: need ${expected_cost:.2f}, have ${self.available_capital:.2f}",
                details={
                    "required": expected_cost,
                    "available": self.available_capital,
                },
            )

        # Check 4: Total exposure limit
        new_exposure = self.state.total_exposure + expected_cost
        if new_exposure > self.limits.max_total_exposure:
            return RiskCheck(
                name="exposure_limit",
                result=RiskCheckResult.BLOCKED,
                level=RiskLevel.HIGH,
                message=f"Total exposure ${new_exposure:.2f} exceeds limit ${self.limits.max_total_exposure:.2f}",
            )

        # Check 5: Market exposure limit
        if market_id:
            market_exposure = (
                self.state.market_exposures.get(market_id, 0) + expected_cost
            )
            if market_exposure > self.limits.max_single_market_exposure:
                return RiskCheck(
                    name="market_exposure",
                    result=RiskCheckResult.BLOCKED,
                    level=RiskLevel.HIGH,
                    message=f"Market exposure ${market_exposure:.2f} exceeds limit",
                )

        # Check 6: Daily loss limit
        if self.state.daily_pnl < 0:
            if abs(self.state.daily_pnl) >= self.limits.max_daily_loss:
                return RiskCheck(
                    name="daily_loss",
                    result=RiskCheckResult.BLOCKED,
                    level=RiskLevel.CRITICAL,
                    message=f"Daily loss limit reached: ${abs(self.state.daily_pnl):.2f}",
                )

            daily_loss_pct = abs(self.state.daily_pnl) / self.state.starting_capital
            if daily_loss_pct >= self.limits.max_daily_loss_pct:
                return RiskCheck(
                    name="daily_loss_pct",
                    result=RiskCheckResult.BLOCKED,
                    level=RiskLevel.CRITICAL,
                    message=f"Daily loss {daily_loss_pct:.1%} exceeds {self.limits.max_daily_loss_pct:.1%}",
                )

        # Check 7: Trade rate limits
        if self.state.trades_this_minute >= self.limits.max_trades_per_minute:
            return RiskCheck(
                name="rate_limit_minute",
                result=RiskCheckResult.BLOCKED,
                level=RiskLevel.MEDIUM,
                message=f"Rate limit: {self.state.trades_this_minute} trades/minute",
            )

        if self.state.trades_this_hour >= self.limits.max_trades_per_hour:
            return RiskCheck(
                name="rate_limit_hour",
                result=RiskCheckResult.BLOCKED,
                level=RiskLevel.MEDIUM,
                message=f"Rate limit: {self.state.trades_this_hour} trades/hour",
            )

        if self.state.trades_today >= self.limits.max_trades_per_day:
            return RiskCheck(
                name="rate_limit_day",
                result=RiskCheckResult.BLOCKED,
                level=RiskLevel.MEDIUM,
                message=f"Daily trade limit reached: {self.state.trades_today}",
            )

        # Check 8: Minimum trade interval
        if self.state.last_trade_time:
            elapsed = (datetime.now() - self.state.last_trade_time).total_seconds()
            if elapsed < self.limits.min_trade_interval_seconds:
                return RiskCheck(
                    name="trade_interval",
                    result=RiskCheckResult.BLOCKED,
                    level=RiskLevel.LOW,
                    message=f"Cooldown: {self.limits.min_trade_interval_seconds - elapsed:.1f}s remaining",
                )

        # Check 9: Consecutive losses
        if self.state.consecutive_losses >= self.limits.consecutive_loss_limit:
            return RiskCheck(
                name="consecutive_losses",
                result=RiskCheckResult.BLOCKED,
                level=RiskLevel.HIGH,
                message=f"Consecutive loss limit: {self.state.consecutive_losses} losses",
            )

        # Check 10: Drawdown circuit breaker
        if self.drawdown >= self.limits.drawdown_halt_pct:
            self.halt_trading(f"Drawdown of {self.drawdown:.1%} exceeded limit")
            return RiskCheck(
                name="drawdown_breaker",
                result=RiskCheckResult.BLOCKED,
                level=RiskLevel.CRITICAL,
                message=f"Circuit breaker: {self.drawdown:.1%} drawdown",
            )

        # All checks passed
        level = RiskLevel.LOW
        if self.drawdown > 0.05 or self.state.consecutive_losses > 2:
            level = RiskLevel.MEDIUM
        if self.drawdown > 0.08 or self.state.consecutive_losses > 3:
            level = RiskLevel.HIGH

        return RiskCheck(
            name="all_checks",
            result=RiskCheckResult.PASSED,
            level=level,
            message="All risk checks passed",
            details={
                "available_capital": self.available_capital,
                "drawdown": f"{self.drawdown:.1%}",
                "trades_today": self.state.trades_today,
            },
        )

    def record_trade(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
        pnl: float,
        fees: float = 0.0,
        market_id: Optional[str] = None,
    ):
        """
        Record a completed trade.

        Args:
            token_id: Token traded
            side: BUY or SELL
            size: Trade size
            price: Execution price
            pnl: Profit/loss from trade
            fees: Fees paid
            market_id: Market ID for exposure tracking
        """
        now = datetime.now()

        # Record trade time
        self._trade_times.append(now)
        self._recent_pnls.append(pnl)

        # Update counters
        self.state.trades_today += 1
        self.state.last_trade_time = now

        # Update P&L
        net_pnl = pnl - fees
        self.state.daily_pnl += net_pnl
        self.state.total_pnl += net_pnl
        self.state.current_capital += net_pnl

        # Track consecutive losses
        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        # Update exposure
        if side == "BUY":
            cost = size * price
            self.state.total_exposure += cost
            if market_id:
                current = self.state.market_exposures.get(market_id, 0)
                self.state.market_exposures[market_id] = current + cost
            self.state.open_position_count += 1
        elif side == "SELL":
            self.state.open_position_count = max(0, self.state.open_position_count - 1)

        # Update risk level
        self._update_risk_level()

        logger.debug(
            f"Trade recorded: {side} {size} @ ${price:.3f}, "
            f"PnL=${net_pnl:.2f}, Daily=${self.state.daily_pnl:.2f}"
        )

    def _update_risk_level(self):
        """Update overall risk level based on current state."""
        if self.state.is_halted:
            self.state.risk_level = RiskLevel.CRITICAL
        elif self.drawdown > 0.08 or self.state.consecutive_losses > 3:
            self.state.risk_level = RiskLevel.HIGH
        elif self.drawdown > 0.05 or self.state.consecutive_losses > 1:
            self.state.risk_level = RiskLevel.MEDIUM
        else:
            self.state.risk_level = RiskLevel.LOW

    def record_position_close(
        self,
        token_id: str,
        market_id: Optional[str] = None,
        exposure_amount: float = 0.0,
    ):
        """Record a position being closed."""
        self.state.total_exposure = max(0, self.state.total_exposure - exposure_amount)

        if market_id and market_id in self.state.market_exposures:
            current = self.state.market_exposures[market_id]
            self.state.market_exposures[market_id] = max(0, current - exposure_amount)

        self.state.open_position_count = max(0, self.state.open_position_count - 1)

    def halt_trading(self, reason: str):
        """Halt all trading."""
        self.state.is_halted = True
        self.state.halt_reason = reason
        self.state.risk_level = RiskLevel.CRITICAL
        logger.warning(f"TRADING HALTED: {reason}")

    def resume_trading(self):
        """Resume trading after halt."""
        self.state.is_halted = False
        self.state.halt_reason = None
        self._update_risk_level()
        logger.info("Trading resumed")

    def get_max_position_size(
        self,
        expected_price: float,
        market_id: Optional[str] = None,
    ) -> float:
        """
        Get maximum allowed position size for current conditions.

        Args:
            expected_price: Expected price per contract
            market_id: Market for exposure calculation

        Returns:
            Maximum position size
        """
        # Start with configured limit
        max_size = self.limits.max_position_size

        # Limit by available capital
        if expected_price > 0:
            from_capital = self.available_capital / expected_price
            max_size = min(max_size, from_capital)

        # Limit by total exposure
        remaining_exposure = self.limits.max_total_exposure - self.state.total_exposure
        if expected_price > 0:
            from_exposure = remaining_exposure / expected_price
            max_size = min(max_size, from_exposure)

        # Limit by market exposure
        if market_id:
            current_market = self.state.market_exposures.get(market_id, 0)
            remaining_market = self.limits.max_single_market_exposure - current_market
            if expected_price > 0:
                from_market = remaining_market / expected_price
                max_size = min(max_size, from_market)

        return max(0, max_size)

    def get_status(self) -> dict:
        """Get current risk status."""
        return {
            "risk_level": self.state.risk_level.value,
            "is_halted": self.state.is_halted,
            "halt_reason": self.state.halt_reason,
            "capital": {
                "starting": f"${self.state.starting_capital:.2f}",
                "current": f"${self.state.current_capital:.2f}",
                "available": f"${self.available_capital:.2f}",
                "drawdown": f"{self.drawdown:.1%}",
            },
            "pnl": {
                "daily": f"${self.state.daily_pnl:.2f}",
                "total": f"${self.state.total_pnl:.2f}",
            },
            "exposure": {
                "total": f"${self.state.total_exposure:.2f}",
                "positions": self.state.open_position_count,
            },
            "activity": {
                "trades_today": self.state.trades_today,
                "trades_hour": self.state.trades_this_hour,
                "consecutive_losses": self.state.consecutive_losses,
            },
        }
