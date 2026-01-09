"""
Metrics collection and tracking for the arbitrage bot.

Collects and exposes metrics for:
- Trading performance
- API latency
- System health
- Opportunity detection
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import deque
import statistics

from loguru import logger


@dataclass
class LatencyMetric:
    """Latency measurement."""
    name: str
    value_ms: float
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TradeMetric:
    """Trade execution metric."""
    timestamp: datetime
    size: float
    profit: float
    latency_ms: float
    success: bool


@dataclass
class BotMetrics:
    """Aggregated bot metrics."""
    # Timing
    uptime_seconds: float = 0.0
    start_time: Optional[datetime] = None

    # Trading
    total_trades: int = 0
    successful_trades: int = 0
    failed_trades: int = 0
    total_volume: float = 0.0
    total_profit: float = 0.0
    total_fees: float = 0.0

    # Opportunities
    opportunities_detected: int = 0
    opportunities_executed: int = 0
    opportunities_missed: int = 0

    # Performance
    win_rate: float = 0.0
    avg_profit_per_trade: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0

    # Latency
    avg_api_latency_ms: float = 0.0
    avg_execution_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0

    # System
    memory_mb: float = 0.0
    cpu_percent: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "uptime": f"{self.uptime_seconds / 3600:.1f}h",
            "trades": {
                "total": self.total_trades,
                "successful": self.successful_trades,
                "failed": self.failed_trades,
                "win_rate": f"{self.win_rate:.1%}",
            },
            "profit": {
                "total": f"${self.total_profit:.2f}",
                "avg_per_trade": f"${self.avg_profit_per_trade:.2f}",
                "volume": f"${self.total_volume:.2f}",
                "fees": f"${self.total_fees:.2f}",
            },
            "opportunities": {
                "detected": self.opportunities_detected,
                "executed": self.opportunities_executed,
                "missed": self.opportunities_missed,
                "execution_rate": (
                    f"{self.opportunities_executed / self.opportunities_detected:.1%}"
                    if self.opportunities_detected > 0 else "N/A"
                ),
            },
            "latency": {
                "avg_api": f"{self.avg_api_latency_ms:.0f}ms",
                "avg_execution": f"{self.avg_execution_latency_ms:.0f}ms",
                "p99": f"{self.p99_latency_ms:.0f}ms",
            },
        }


class MetricsCollector:
    """
    Collects and aggregates metrics for the bot.

    Example:
        collector = MetricsCollector()

        # Record metrics
        with collector.measure_latency("api_call"):
            await api_call()

        collector.record_trade(profit=1.5, size=10, success=True)

        # Get metrics
        metrics = collector.get_metrics()
        print(f"Win rate: {metrics.win_rate:.1%}")
    """

    def __init__(
        self,
        history_size: int = 10000,
        latency_window_size: int = 1000,
    ):
        self._start_time = datetime.now()
        self._history_size = history_size

        # Trade history
        self._trades: deque = deque(maxlen=history_size)
        self._profits: deque = deque(maxlen=history_size)

        # Latency tracking
        self._api_latencies: deque = deque(maxlen=latency_window_size)
        self._execution_latencies: deque = deque(maxlen=latency_window_size)

        # Counters
        self._total_trades = 0
        self._successful_trades = 0
        self._failed_trades = 0
        self._total_volume = 0.0
        self._total_profit = 0.0
        self._total_fees = 0.0

        self._opportunities_detected = 0
        self._opportunities_executed = 0
        self._opportunities_missed = 0

        # Peak tracking
        self._peak_capital = 0.0
        self._max_drawdown = 0.0

    @property
    def uptime(self) -> timedelta:
        """Bot uptime."""
        return datetime.now() - self._start_time

    class LatencyContext:
        """Context manager for measuring latency."""

        def __init__(self, collector: "MetricsCollector", name: str):
            self.collector = collector
            self.name = name
            self.start_time = None

        def __enter__(self):
            self.start_time = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if self.start_time:
                elapsed_ms = (time.perf_counter() - self.start_time) * 1000
                self.collector.record_latency(self.name, elapsed_ms)

        async def __aenter__(self):
            self.start_time = time.perf_counter()
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if self.start_time:
                elapsed_ms = (time.perf_counter() - self.start_time) * 1000
                self.collector.record_latency(self.name, elapsed_ms)

    def measure_latency(self, name: str) -> LatencyContext:
        """Create a context manager for measuring latency."""
        return self.LatencyContext(self, name)

    def record_latency(self, name: str, latency_ms: float):
        """Record a latency measurement."""
        metric = LatencyMetric(name=name, value_ms=latency_ms)

        if "api" in name.lower():
            self._api_latencies.append(latency_ms)
        elif "execution" in name.lower() or "trade" in name.lower():
            self._execution_latencies.append(latency_ms)

    def record_trade(
        self,
        profit: float,
        size: float,
        fees: float = 0.0,
        latency_ms: float = 0.0,
        success: bool = True,
    ):
        """Record a trade execution."""
        self._total_trades += 1
        self._total_volume += size

        if success:
            self._successful_trades += 1
            self._total_profit += profit
            self._total_fees += fees
            self._profits.append(profit - fees)
        else:
            self._failed_trades += 1

        if latency_ms > 0:
            self._execution_latencies.append(latency_ms)

        trade = TradeMetric(
            timestamp=datetime.now(),
            size=size,
            profit=profit - fees,
            latency_ms=latency_ms,
            success=success,
        )
        self._trades.append(trade)

        # Update drawdown tracking
        self._update_drawdown()

    def _update_drawdown(self):
        """Update max drawdown calculation."""
        if not self._profits:
            return

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for profit in self._profits:
            cumulative += profit
            peak = max(peak, cumulative)
            drawdown = (peak - cumulative) / peak if peak > 0 else 0
            max_dd = max(max_dd, drawdown)

        self._max_drawdown = max_dd

    def record_opportunity(self, detected: bool = True, executed: bool = False):
        """Record an opportunity detection/execution."""
        if detected:
            self._opportunities_detected += 1
        if executed:
            self._opportunities_executed += 1
        elif detected:
            self._opportunities_missed += 1

    def _calculate_sharpe(self) -> float:
        """Calculate Sharpe ratio."""
        if len(self._profits) < 2:
            return 0.0

        returns = list(self._profits)
        avg_return = statistics.mean(returns)
        std_return = statistics.stdev(returns)

        if std_return == 0:
            return 0.0

        # Annualized (assuming ~8760 hours/year of trading)
        annualization = (8760 / max(1, self.uptime.total_seconds() / 3600)) ** 0.5
        return (avg_return / std_return) * annualization

    def _calculate_percentile(
        self,
        data: deque,
        percentile: float,
    ) -> float:
        """Calculate percentile of data."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile / 100)
        return sorted_data[min(index, len(sorted_data) - 1)]

    def get_metrics(self) -> BotMetrics:
        """Get current aggregated metrics."""
        # Calculate latency stats
        avg_api = (
            statistics.mean(self._api_latencies)
            if self._api_latencies else 0.0
        )
        avg_exec = (
            statistics.mean(self._execution_latencies)
            if self._execution_latencies else 0.0
        )
        p99 = self._calculate_percentile(self._execution_latencies, 99)

        # Calculate win rate
        win_rate = (
            self._successful_trades / self._total_trades
            if self._total_trades > 0 else 0.0
        )

        # Calculate avg profit
        avg_profit = (
            self._total_profit / self._successful_trades
            if self._successful_trades > 0 else 0.0
        )

        return BotMetrics(
            uptime_seconds=self.uptime.total_seconds(),
            start_time=self._start_time,
            total_trades=self._total_trades,
            successful_trades=self._successful_trades,
            failed_trades=self._failed_trades,
            total_volume=self._total_volume,
            total_profit=self._total_profit,
            total_fees=self._total_fees,
            opportunities_detected=self._opportunities_detected,
            opportunities_executed=self._opportunities_executed,
            opportunities_missed=self._opportunities_missed,
            win_rate=win_rate,
            avg_profit_per_trade=avg_profit,
            sharpe_ratio=self._calculate_sharpe(),
            max_drawdown=self._max_drawdown,
            avg_api_latency_ms=avg_api,
            avg_execution_latency_ms=avg_exec,
            p99_latency_ms=p99,
        )

    def get_recent_trades(self, limit: int = 10) -> List[TradeMetric]:
        """Get recent trades."""
        return list(self._trades)[-limit:]

    def reset(self):
        """Reset all metrics."""
        self._trades.clear()
        self._profits.clear()
        self._api_latencies.clear()
        self._execution_latencies.clear()
        self._total_trades = 0
        self._successful_trades = 0
        self._failed_trades = 0
        self._total_volume = 0.0
        self._total_profit = 0.0
        self._total_fees = 0.0
        self._opportunities_detected = 0
        self._opportunities_executed = 0
        self._opportunities_missed = 0
        self._start_time = datetime.now()
        logger.info("Metrics reset")
