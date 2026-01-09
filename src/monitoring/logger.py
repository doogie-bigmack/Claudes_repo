"""
Logging configuration for the arbitrage bot.

Uses loguru for enhanced logging with:
- Colored console output
- File rotation
- Structured logging
- Performance tracking
"""

import sys
from pathlib import Path
from typing import Optional

from loguru import logger


def setup_logging(
    level: str = "INFO",
    log_dir: Optional[str] = None,
    enable_file_logging: bool = True,
    enable_json_logging: bool = False,
) -> None:
    """
    Configure logging for the bot.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Directory for log files
        enable_file_logging: Write logs to files
        enable_json_logging: Use JSON format for files
    """
    # Remove default handler
    logger.remove()

    # Console handler with colors
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stderr,
        format=log_format,
        level=level,
        colorize=True,
    )

    if enable_file_logging:
        # Create log directory
        log_path = Path(log_dir) if log_dir else Path("logs")
        log_path.mkdir(exist_ok=True)

        # Main log file with rotation
        logger.add(
            log_path / "bot_{time:YYYY-MM-DD}.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
            level=level,
            rotation="00:00",  # Rotate at midnight
            retention="30 days",
            compression="gz",
        )

        # Error log
        logger.add(
            log_path / "errors_{time:YYYY-MM-DD}.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
            level="ERROR",
            rotation="00:00",
            retention="30 days",
        )

        # Trade log (separate file for trade records)
        logger.add(
            log_path / "trades_{time:YYYY-MM-DD}.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
            level="INFO",
            filter=lambda record: "trade" in record["extra"],
            rotation="00:00",
            retention="90 days",
        )

        if enable_json_logging:
            logger.add(
                log_path / "bot_{time:YYYY-MM-DD}.json",
                format="{message}",
                level=level,
                serialize=True,
                rotation="00:00",
                retention="30 days",
            )

    logger.info(f"Logging initialized at {level} level")


def get_logger(name: str = "bot"):
    """Get a logger instance with a specific name."""
    return logger.bind(name=name)


class TradeLogger:
    """Specialized logger for trade records."""

    def __init__(self):
        self._logger = logger.bind(trade=True)

    def log_opportunity(
        self,
        market_id: str,
        yes_price: float,
        no_price: float,
        spread: float,
        estimated_profit: float,
    ):
        """Log an arbitrage opportunity."""
        self._logger.info(
            f"OPPORTUNITY | market={market_id} | "
            f"yes=${yes_price:.3f} | no=${no_price:.3f} | "
            f"spread={spread:.2%} | profit=${estimated_profit:.2f}"
        )

    def log_trade(
        self,
        trade_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
        status: str,
    ):
        """Log a trade execution."""
        self._logger.info(
            f"TRADE | id={trade_id} | token={token_id[:16]}... | "
            f"{side} {size} @ ${price:.3f} | status={status}"
        )

    def log_pnl(
        self,
        trade_id: str,
        gross_pnl: float,
        fees: float,
        net_pnl: float,
    ):
        """Log trade P&L."""
        self._logger.info(
            f"PNL | trade={trade_id} | "
            f"gross=${gross_pnl:.2f} | fees=${fees:.2f} | net=${net_pnl:.2f}"
        )


# Create global trade logger instance
trade_logger = TradeLogger()
