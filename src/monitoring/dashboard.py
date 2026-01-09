"""
Terminal dashboard for real-time bot monitoring.

Provides a live view of:
- Current opportunities
- Recent trades
- Performance metrics
- Risk status
"""

import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich import box
from loguru import logger

from .metrics import MetricsCollector, BotMetrics
from ..risk.risk_manager import RiskManager, RiskLevel
from ..arbitrage.detector import ArbitrageDetector, ArbitrageOpportunity


class Dashboard:
    """
    Terminal-based dashboard for monitoring the bot.

    Example:
        dashboard = Dashboard(
            metrics=metrics_collector,
            risk=risk_manager,
            detector=detector,
        )

        # Run dashboard
        await dashboard.run()
    """

    def __init__(
        self,
        metrics: Optional[MetricsCollector] = None,
        risk: Optional[RiskManager] = None,
        detector: Optional[ArbitrageDetector] = None,
        refresh_rate: float = 1.0,
    ):
        self.console = Console()
        self.metrics = metrics
        self.risk = risk
        self.detector = detector
        self.refresh_rate = refresh_rate
        self._running = False

    def _create_header(self) -> Panel:
        """Create dashboard header."""
        title = Text("Polymarket Arbitrage Bot", style="bold magenta")
        subtitle = Text(
            f" | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            style="dim"
        )
        title.append(subtitle)

        return Panel(
            title,
            box=box.DOUBLE,
            style="blue",
        )

    def _create_metrics_table(self) -> Table:
        """Create performance metrics table."""
        table = Table(
            title="Performance Metrics",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )

        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right")

        if self.metrics:
            m = self.metrics.get_metrics()

            table.add_row("Uptime", f"{m.uptime_seconds / 3600:.1f}h")
            table.add_row("Total Trades", str(m.total_trades))
            table.add_row("Win Rate", f"{m.win_rate:.1%}")
            table.add_row("─" * 15, "─" * 10)
            table.add_row("Total Profit", f"${m.total_profit:.2f}")
            table.add_row("Avg Profit/Trade", f"${m.avg_profit_per_trade:.2f}")
            table.add_row("Total Volume", f"${m.total_volume:.2f}")
            table.add_row("Total Fees", f"${m.total_fees:.2f}")
            table.add_row("─" * 15, "─" * 10)
            table.add_row("Opportunities", str(m.opportunities_detected))
            table.add_row("Executed", str(m.opportunities_executed))
            table.add_row("Execution Rate",
                f"{m.opportunities_executed / max(1, m.opportunities_detected):.1%}"
            )
            table.add_row("─" * 15, "─" * 10)
            table.add_row("Avg Latency", f"{m.avg_execution_latency_ms:.0f}ms")
            table.add_row("P99 Latency", f"{m.p99_latency_ms:.0f}ms")
        else:
            table.add_row("No metrics", "N/A")

        return table

    def _create_risk_table(self) -> Table:
        """Create risk status table."""
        table = Table(
            title="Risk Status",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )

        table.add_column("Parameter", style="dim")
        table.add_column("Value", justify="right")

        if self.risk:
            status = self.risk.get_status()

            # Risk level with color
            level = status["risk_level"]
            level_color = {
                "low": "green",
                "medium": "yellow",
                "high": "red",
                "critical": "bold red",
            }.get(level, "white")
            table.add_row("Risk Level", f"[{level_color}]{level.upper()}[/]")

            if status["is_halted"]:
                table.add_row("Status", "[bold red]HALTED[/]")
                table.add_row("Reason", status.get("halt_reason", "Unknown"))
            else:
                table.add_row("Status", "[green]ACTIVE[/]")

            table.add_row("─" * 15, "─" * 10)
            table.add_row("Starting Capital", status["capital"]["starting"])
            table.add_row("Current Capital", status["capital"]["current"])
            table.add_row("Available", status["capital"]["available"])
            table.add_row("Drawdown", status["capital"]["drawdown"])
            table.add_row("─" * 15, "─" * 10)
            table.add_row("Daily P&L", status["pnl"]["daily"])
            table.add_row("Total P&L", status["pnl"]["total"])
            table.add_row("─" * 15, "─" * 10)
            table.add_row("Total Exposure", status["exposure"]["total"])
            table.add_row("Open Positions", str(status["exposure"]["positions"]))
            table.add_row("─" * 15, "─" * 10)
            table.add_row("Trades Today", str(status["activity"]["trades_today"]))
            table.add_row("Consecutive Losses", str(status["activity"]["consecutive_losses"]))
        else:
            table.add_row("No risk manager", "N/A")

        return table

    def _create_opportunities_table(self) -> Table:
        """Create opportunities table."""
        table = Table(
            title="Active Opportunities",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )

        table.add_column("Market", max_width=30)
        table.add_column("YES", justify="right")
        table.add_column("NO", justify="right")
        table.add_column("Spread", justify="right")
        table.add_column("Profit", justify="right")
        table.add_column("Status")

        if self.detector:
            opps = self.detector.get_opportunities()[:10]

            for opp in opps:
                spread_color = "green" if opp.spread > 0.02 else "yellow"
                profit_color = "green" if opp.is_profitable else "red"

                table.add_row(
                    opp.market_pair.question[:30],
                    f"${opp.yes_price:.3f}",
                    f"${opp.no_price:.3f}",
                    f"[{spread_color}]{opp.spread:.2%}[/]",
                    f"[{profit_color}]${opp.estimated_profit:.2f}[/]",
                    opp.status.value,
                )

            if not opps:
                table.add_row("No active opportunities", "", "", "", "", "")
        else:
            table.add_row("No detector", "", "", "", "", "")

        return table

    def _create_recent_trades_table(self) -> Table:
        """Create recent trades table."""
        table = Table(
            title="Recent Trades",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )

        table.add_column("Time", style="dim")
        table.add_column("Size", justify="right")
        table.add_column("Profit", justify="right")
        table.add_column("Latency", justify="right")
        table.add_column("Status")

        if self.metrics:
            trades = self.metrics.get_recent_trades(10)

            for trade in reversed(trades):
                profit_color = "green" if trade.profit > 0 else "red"
                status_text = "[green]✓[/]" if trade.success else "[red]✗[/]"

                table.add_row(
                    trade.timestamp.strftime("%H:%M:%S"),
                    f"{trade.size:.1f}",
                    f"[{profit_color}]${trade.profit:.2f}[/]",
                    f"{trade.latency_ms:.0f}ms",
                    status_text,
                )

            if not trades:
                table.add_row("No trades yet", "", "", "", "")
        else:
            table.add_row("No metrics", "", "", "", "")

        return table

    def _create_layout(self) -> Layout:
        """Create the dashboard layout."""
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )

        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )

        layout["left"].split_column(
            Layout(name="metrics"),
            Layout(name="risk"),
        )

        layout["right"].split_column(
            Layout(name="opportunities"),
            Layout(name="trades"),
        )

        return layout

    def render(self) -> Layout:
        """Render the full dashboard."""
        layout = self._create_layout()

        layout["header"].update(self._create_header())
        layout["metrics"].update(self._create_metrics_table())
        layout["risk"].update(self._create_risk_table())
        layout["opportunities"].update(self._create_opportunities_table())
        layout["trades"].update(self._create_recent_trades_table())

        # Footer with controls
        footer_text = Text(
            "Press Ctrl+C to stop | "
            "Q to quit | "
            "R to reset metrics",
            style="dim",
        )
        layout["footer"].update(Panel(footer_text, box=box.SIMPLE))

        return layout

    async def run(self):
        """Run the live dashboard."""
        self._running = True

        with Live(
            self.render(),
            console=self.console,
            refresh_per_second=1 / self.refresh_rate,
            screen=True,
        ) as live:
            while self._running:
                live.update(self.render())
                await asyncio.sleep(self.refresh_rate)

    def stop(self):
        """Stop the dashboard."""
        self._running = False

    def print_summary(self):
        """Print a one-time summary (non-live)."""
        self.console.print(self._create_header())
        self.console.print()

        # Create side-by-side layout
        table = Table.grid(expand=True)
        table.add_column()
        table.add_column()

        table.add_row(
            self._create_metrics_table(),
            self._create_risk_table(),
        )
        self.console.print(table)
        self.console.print()

        self.console.print(self._create_opportunities_table())
        self.console.print()
        self.console.print(self._create_recent_trades_table())


def print_status(
    metrics: Optional[MetricsCollector] = None,
    risk: Optional[RiskManager] = None,
    detector: Optional[ArbitrageDetector] = None,
):
    """Print a status summary without live updates."""
    dashboard = Dashboard(
        metrics=metrics,
        risk=risk,
        detector=detector,
    )
    dashboard.print_summary()
