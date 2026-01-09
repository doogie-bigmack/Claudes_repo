"""
Polymarket Arbitrage Bot - Main Entry Point

This is the main orchestrator that ties all components together:
- Market discovery and monitoring
- Arbitrage detection
- Trade execution
- Risk management
- Monitoring and logging

Usage:
    python -m src.main [--dry-run] [--config config.yaml]

Or use the CLI:
    python -m src.main run --capital 1000 --dry-run
"""

import asyncio
import signal
from typing import Optional

import typer
from loguru import logger

# Local imports
from config import BotConfig, get_config

from .api.clob_client import CLOBClient
from .api.gamma_client import GammaClient, Market
from .api.websocket_client import WebSocketClient
from .arbitrage.calculator import FeeStructure
from .arbitrage.detector import (ArbitrageDetector, ArbitrageOpportunity,
                                 MarketPair)
from .arbitrage.strategies import IntraMarketArbitrage, StrategyManager
from .execution.order_manager import OrderManager
from .execution.wallet import WalletManager
from .monitoring.dashboard import Dashboard, print_status
from .monitoring.logger import setup_logging
from .monitoring.metrics import MetricsCollector
from .risk.risk_manager import RiskLimits, RiskManager


class ArbitrageBot:
    """
    Main arbitrage bot orchestrator.

    Coordinates all components for automated arbitrage trading.

    Example:
        bot = ArbitrageBot(config)
        await bot.run()
    """

    def __init__(
        self,
        config: Optional[BotConfig] = None,
        dry_run: bool = True,
    ):
        """
        Initialize the bot.

        Args:
            config: Bot configuration
            dry_run: Run without executing real trades
        """
        self.config = config or get_config()
        self.dry_run = dry_run or self.config.dry_run

        # Core components
        self.gamma_client: Optional[GammaClient] = None
        self.clob_client: Optional[CLOBClient] = None
        self.ws_client: Optional[WebSocketClient] = None

        # Trading components
        self.wallet_manager: Optional[WalletManager] = None
        self.order_manager: Optional[OrderManager] = None
        self.detector: Optional[ArbitrageDetector] = None
        self.strategy_manager: Optional[StrategyManager] = None
        self.risk_manager: Optional[RiskManager] = None

        # Monitoring
        self.metrics = MetricsCollector()
        self.dashboard: Optional[Dashboard] = None

        # State
        self._running = False
        self._initialized = False
        self._market_pairs: dict = {}

        logger.info(f"Bot initialized (dry_run={self.dry_run})")

    async def initialize(self) -> bool:
        """
        Initialize all components.

        Returns:
            True if initialization successful
        """
        logger.info("Initializing bot components...")

        try:
            # Set up logging
            setup_logging(
                level=self.config.monitoring.log_level,
                enable_file_logging=True,
            )

            # Initialize API clients
            self.gamma_client = GammaClient(
                base_url=self.config.api.gamma_api_url,
            )
            await self.gamma_client.__aenter__()

            self.clob_client = CLOBClient(
                private_key=self.config.wallet.private_key,
                wallet_address=self.config.wallet.wallet_address,
                base_url=self.config.api.clob_api_url,
            )
            await self.clob_client.__aenter__()

            # Initialize wallet (if not dry run)
            if not self.dry_run and self.config.wallet.private_key:
                self.wallet_manager = WalletManager(
                    private_key=self.config.wallet.private_key,
                    rpc_url=self.config.wallet.polygon_rpc_url,
                )
                ready = await self.wallet_manager.initialize()
                if not ready:
                    logger.warning("Wallet not ready - switching to dry run mode")
                    self.dry_run = True
            else:
                self.wallet_manager = WalletManager()

            # Initialize order manager
            self.order_manager = OrderManager(
                clob_client=self.clob_client,
                wallet_manager=self.wallet_manager,
                dry_run=self.dry_run,
            )

            # Initialize fee structure based on config
            fee_structure = FeeStructure(
                taker_fee=self.config.trading.taker_fee_rate,
                maker_rebate=abs(self.config.trading.maker_fee_rate),
                gas_cost_usdc=self.config.trading.gas_estimate,
            )

            # Initialize arbitrage detector
            self.detector = ArbitrageDetector(
                min_profit_threshold=self.config.trading.min_profit_threshold,
                min_liquidity=self.config.market_filter.min_liquidity,
                max_position_size=self.config.trading.max_position_size,
                fee_structure=fee_structure,
            )

            # Register opportunity callback
            self.detector.on_opportunity(self._on_opportunity)

            # Initialize strategy manager
            self.strategy_manager = StrategyManager()
            self.strategy_manager.register(
                IntraMarketArbitrage(
                    max_position_size=self.config.trading.max_position_size,
                    min_profit_threshold=self.config.trading.min_profit_threshold,
                ),
                default=True,
            )

            # Initialize risk manager
            risk_limits = RiskLimits(
                max_position_size=self.config.trading.max_position_size,
                max_daily_loss=self.config.trading.total_capital
                * self.config.risk.max_daily_loss,
                max_daily_loss_pct=self.config.risk.max_daily_loss,
                max_trades_per_hour=self.config.risk.max_trades_per_hour,
                min_trade_interval_seconds=self.config.risk.trade_cooldown,
            )
            self.risk_manager = RiskManager(
                limits=risk_limits,
                starting_capital=self.config.trading.total_capital,
            )

            # Initialize WebSocket client
            self.ws_client = WebSocketClient(
                ws_url=self.config.api.clob_ws_url,
            )
            self.ws_client.on_price_update(self._on_price_update)

            # Initialize dashboard
            self.dashboard = Dashboard(
                metrics=self.metrics,
                risk=self.risk_manager,
                detector=self.detector,
            )

            self._initialized = True
            logger.info("Bot initialization complete")
            return True

        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            return False

    async def discover_markets(self) -> int:
        """
        Discover and register target markets.

        Returns:
            Number of markets discovered
        """
        logger.info("Discovering target markets...")

        if not self.gamma_client:
            return 0

        markets_found = 0
        target_types = self.config.market_filter.target_market_list

        for market_type in target_types:
            try:
                # Parse market type (e.g., "btc_hourly" -> crypto="btc", type="hourly")
                if "_" in market_type:
                    crypto, mtype = market_type.split("_", 1)
                else:
                    crypto, mtype = market_type, "hourly"

                # Fetch markets
                markets = await self.gamma_client.get_crypto_markets(crypto, mtype)
                logger.info(f"Found {len(markets)} {market_type} markets")

                for market in markets:
                    if (
                        market.is_binary
                        and market.liquidity >= self.config.market_filter.min_liquidity
                    ):
                        # Create market pair
                        pair = self._create_market_pair(market)
                        if pair:
                            self._market_pairs[market.condition_id] = pair
                            self.detector.add_market_pair(pair)
                            markets_found += 1

            except Exception as e:
                logger.warning(f"Error discovering {market_type} markets: {e}")

        logger.info(f"Registered {markets_found} market pairs")
        return markets_found

    def _create_market_pair(self, market: Market) -> Optional[MarketPair]:
        """Create a MarketPair from a Market object."""
        if len(market.outcomes) < 2:
            return None

        yes_outcome = None
        no_outcome = None

        for outcome in market.outcomes:
            if outcome.outcome.lower() == "yes":
                yes_outcome = outcome
            elif outcome.outcome.lower() == "no":
                no_outcome = outcome

        if not yes_outcome or not no_outcome:
            return None

        return MarketPair(
            market_id=market.condition_id,
            question=market.question,
            yes_token_id=yes_outcome.token_id,
            no_token_id=no_outcome.token_id,
            yes_price=yes_outcome.price,
            no_price=no_outcome.price,
            liquidity=market.liquidity,
            end_time=market.end_date,
        )

    def _on_price_update(self, update):
        """Handle real-time price updates."""
        if self.detector:
            self.detector.update_price(update.token_id, update.price)

    async def _on_opportunity(self, opportunity: ArbitrageOpportunity):
        """Handle detected arbitrage opportunity."""
        logger.info(
            f"Opportunity detected: {opportunity.market_pair.question[:40]}... "
            f"spread={opportunity.spread:.2%} profit=${opportunity.estimated_profit:.2f}"
        )

        self.metrics.record_opportunity(detected=True)

        # Check risk limits
        if not self.risk_manager:
            return

        check = self.risk_manager.check_trade(
            token_id=opportunity.market_pair.yes_token_id,
            size=opportunity.recommended_size,
            expected_cost=opportunity.combined_price * opportunity.recommended_size,
            market_id=opportunity.market_pair.market_id,
        )

        if check.blocked:
            logger.warning(f"Trade blocked: {check.message}")
            return

        # Execute if meets criteria
        if opportunity.is_profitable and self.strategy_manager and self.order_manager:
            await self._execute_opportunity(opportunity)

    async def _execute_opportunity(self, opportunity: ArbitrageOpportunity):
        """Execute an arbitrage opportunity."""
        logger.info(f"Executing opportunity: {opportunity.id}")

        pair = opportunity.market_pair

        # Mark as executing
        self.detector.mark_executing(opportunity.id)

        try:
            # Execute through order manager
            async with self.metrics.measure_latency("execution"):
                yes_result, no_result = await self.order_manager.execute_arbitrage_pair(
                    yes_token=pair.yes_token_id,
                    no_token=pair.no_token_id,
                    yes_price=pair.yes_price,
                    no_price=pair.no_price,
                    size=opportunity.recommended_size,
                )

            # Check results
            if yes_result.success and no_result.success:
                # Calculate actual profit
                actual_profit = (
                    opportunity.calculation.net_profit if opportunity.calculation else 0
                )

                # Record in metrics
                self.metrics.record_trade(
                    profit=actual_profit,
                    size=opportunity.recommended_size * 2,  # Both sides
                    latency_ms=yes_result.latency_ms,
                    success=True,
                )

                # Record in risk manager
                if self.risk_manager:
                    self.risk_manager.record_trade(
                        token_id=pair.yes_token_id,
                        side="BUY",
                        size=opportunity.recommended_size,
                        price=pair.yes_price,
                        pnl=actual_profit / 2,
                    )
                    self.risk_manager.record_trade(
                        token_id=pair.no_token_id,
                        side="BUY",
                        size=opportunity.recommended_size,
                        price=pair.no_price,
                        pnl=actual_profit / 2,
                    )

                self.detector.mark_executed(
                    opportunity.id, latency_ms=yes_result.latency_ms
                )
                self.metrics.record_opportunity(detected=False, executed=True)

                logger.info(
                    f"Opportunity executed successfully: "
                    f"profit=${actual_profit:.2f}"
                )

            else:
                self.detector.mark_failed(opportunity.id, "Execution failed")
                self.metrics.record_trade(profit=0, size=0, success=False)
                logger.warning("Opportunity execution failed")

        except Exception as e:
            logger.error(f"Execution error: {e}")
            self.detector.mark_failed(opportunity.id, str(e))

    async def _poll_markets(self):
        """Poll markets for price updates (fallback if WebSocket not available)."""
        while self._running:
            try:
                for market_id, pair in self._market_pairs.items():
                    # Get order books
                    books = await self.clob_client.get_order_books(
                        [pair.yes_token_id, pair.no_token_id]
                    )

                    # Update prices
                    if pair.yes_token_id in books:
                        yes_book = books[pair.yes_token_id]
                        if yes_book.best_ask:
                            self.detector.update_price(
                                pair.yes_token_id, yes_book.best_ask
                            )

                    if pair.no_token_id in books:
                        no_book = books[pair.no_token_id]
                        if no_book.best_ask:
                            self.detector.update_price(
                                pair.no_token_id, no_book.best_ask
                            )

                # Rate limit
                await asyncio.sleep(1.0)

            except Exception as e:
                logger.warning(f"Market poll error: {e}")
                await asyncio.sleep(5.0)

    async def _run_ws_feed(self):
        """Run WebSocket price feed."""
        if not self.ws_client:
            return

        # Subscribe to all markets
        all_tokens = []
        for pair in self._market_pairs.values():
            all_tokens.extend([pair.yes_token_id, pair.no_token_id])

        await self.ws_client.connect()
        await self.ws_client.subscribe_markets(all_tokens)
        await self.ws_client.run_forever()

    async def run(self, use_dashboard: bool = False):
        """
        Run the bot main loop.

        Args:
            use_dashboard: Show live terminal dashboard
        """
        if not self._initialized:
            if not await self.initialize():
                logger.error("Failed to initialize bot")
                return

        self._running = True

        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop)

        logger.info("Starting bot...")

        # Discover markets
        markets_count = await self.discover_markets()
        if markets_count == 0:
            logger.warning("No markets found - check configuration")

        # Create tasks
        tasks = [
            asyncio.create_task(self._poll_markets()),
            asyncio.create_task(self.detector.run_cleanup_loop()),
        ]

        # Optionally add WebSocket
        # tasks.append(asyncio.create_task(self._run_ws_feed()))

        # Run dashboard if requested
        if use_dashboard and self.dashboard:
            tasks.append(asyncio.create_task(self.dashboard.run()))

        try:
            logger.info(f"Bot running. Monitoring {markets_count} markets...")

            # Wait for tasks
            await asyncio.gather(*tasks)

        except asyncio.CancelledError:
            logger.info("Bot stopped")
        finally:
            await self.shutdown()

    def stop(self):
        """Stop the bot."""
        logger.info("Stopping bot...")
        self._running = False

    async def shutdown(self):
        """Clean shutdown of all components."""
        logger.info("Shutting down...")

        # Close API clients
        if self.gamma_client:
            await self.gamma_client.close()
        if self.clob_client:
            await self.clob_client.close()
        if self.ws_client:
            await self.ws_client.disconnect()

        # Print final summary
        if self.metrics:
            print_status(
                metrics=self.metrics,
                risk=self.risk_manager,
                detector=self.detector,
            )

        logger.info("Shutdown complete")


# CLI Application
app = typer.Typer(
    name="polymarket-arb",
    help="Polymarket Arbitrage Trading Bot",
)


@app.command()
def run(
    dry_run: bool = typer.Option(
        True, "--dry-run/--live", help="Run in simulation mode"
    ),
    capital: float = typer.Option(
        1000.0, "--capital", "-c", help="Starting capital in USDC"
    ),
    dashboard: bool = typer.Option(
        False, "--dashboard", "-d", help="Show live dashboard"
    ),
    log_level: str = typer.Option("INFO", "--log-level", "-l", help="Log level"),
):
    """Run the arbitrage bot."""
    # Set up basic logging first
    setup_logging(level=log_level)

    logger.info("Starting Polymarket Arbitrage Bot")
    logger.info(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    logger.info(f"  Capital: ${capital:.2f}")

    # Load config
    config = get_config()
    config.dry_run = dry_run
    config.trading.total_capital = capital

    # Create and run bot
    bot = ArbitrageBot(config=config, dry_run=dry_run)

    asyncio.run(bot.run(use_dashboard=dashboard))


@app.command()
def status():
    """Show current bot status."""
    logger.info("Fetching status...")

    metrics = MetricsCollector()
    print_status(metrics=metrics)


@app.command()
def test_connection():
    """Test API connectivity."""

    async def _test():
        logger.info("Testing Polymarket API connectivity...")

        async with GammaClient() as client:
            markets = await client.get_active_markets(limit=5)
            logger.info(f"✓ Gamma API: Found {len(markets)} markets")

            for market in markets[:3]:
                logger.info(f"  - {market.question[:50]}...")

        logger.info("Connection test complete!")

    asyncio.run(_test())


def main():
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
