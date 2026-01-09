"""
Configuration settings for Polymarket Arbitrage Bot.
Uses pydantic-settings for type-safe configuration management.
"""

from decimal import Decimal
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WalletConfig(BaseSettings):
    """Wallet and blockchain configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    private_key: str = Field(default="", description="Polygon wallet private key")
    wallet_address: str = Field(default="", description="Wallet address")

    # Polygon network settings
    polygon_rpc_url: str = Field(
        default="https://polygon-rpc.com",
        description="Polygon RPC endpoint"
    )
    chain_id: int = Field(default=137, description="Polygon chain ID")


class APIConfig(BaseSettings):
    """API endpoint configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Polymarket endpoints
    gamma_api_url: str = Field(
        default="https://gamma-api.polymarket.com",
        description="Gamma API for market data"
    )
    clob_api_url: str = Field(
        default="https://clob.polymarket.com",
        description="CLOB API for order book"
    )
    clob_ws_url: str = Field(
        default="wss://ws-subscriptions-clob.polymarket.com/ws",
        description="WebSocket for real-time updates"
    )

    # Optional Kalshi for cross-platform arb
    kalshi_api_key: Optional[str] = Field(default=None)
    kalshi_api_secret: Optional[str] = Field(default=None)
    kalshi_api_url: str = Field(
        default="https://trading-api.kalshi.com/trade-api/v2",
        description="Kalshi API endpoint"
    )


class TradingConfig(BaseSettings):
    """Trading parameters configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Profit thresholds
    min_profit_threshold: float = Field(
        default=0.005,
        ge=0.0,
        le=1.0,
        description="Minimum profit threshold (0.5% default)"
    )

    # Position sizing
    max_position_size: float = Field(
        default=100.0,
        gt=0,
        description="Maximum position size per trade in USDC"
    )
    total_capital: float = Field(
        default=1000.0,
        gt=0,
        description="Total trading capital in USDC"
    )
    max_capital_per_trade: float = Field(
        default=0.10,
        gt=0,
        le=1.0,
        description="Maximum percentage of capital per trade"
    )

    # Execution
    slippage_tolerance: float = Field(
        default=0.005,
        ge=0,
        le=0.1,
        description="Maximum slippage tolerance"
    )

    # Fees
    taker_fee_rate: float = Field(
        default=0.0315,
        ge=0,
        le=0.1,
        description="Polymarket taker fee rate"
    )
    maker_fee_rate: float = Field(
        default=-0.005,
        ge=-0.1,
        le=0.1,
        description="Polymarket maker rebate (negative = rebate)"
    )
    gas_estimate: float = Field(
        default=0.001,
        ge=0,
        description="Estimated gas cost per trade in MATIC"
    )


class RiskConfig(BaseSettings):
    """Risk management configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    max_daily_loss: float = Field(
        default=0.05,
        ge=0,
        le=1.0,
        description="Maximum daily loss as percentage of capital"
    )
    max_open_positions: int = Field(
        default=10,
        ge=1,
        description="Maximum concurrent open positions"
    )
    trade_cooldown: int = Field(
        default=5,
        ge=0,
        description="Cooldown between trades in seconds"
    )
    max_trades_per_hour: int = Field(
        default=100,
        ge=1,
        description="Maximum trades per hour"
    )
    stop_loss_threshold: float = Field(
        default=0.1,
        ge=0,
        le=1.0,
        description="Stop trading if daily loss exceeds this"
    )


class MarketFilterConfig(BaseSettings):
    """Market filtering configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    target_markets: str = Field(
        default="btc_hourly,eth_hourly",
        description="Comma-separated target market types"
    )
    min_liquidity: float = Field(
        default=500.0,
        ge=0,
        description="Minimum market liquidity in USDC"
    )
    max_time_to_resolution: int = Field(
        default=3600,
        ge=0,
        description="Maximum seconds until market resolves"
    )

    @property
    def target_market_list(self) -> List[str]:
        """Parse target markets into list."""
        return [m.strip() for m in self.target_markets.split(",")]


class MonitoringConfig(BaseSettings):
    """Monitoring and logging configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: str = Field(default="INFO", description="Logging level")
    telegram_bot_token: Optional[str] = Field(default=None)
    telegram_chat_id: Optional[str] = Field(default=None)
    metrics_export_interval: int = Field(
        default=60,
        ge=10,
        description="Metrics export interval in seconds"
    )


class BotConfig(BaseSettings):
    """Main bot configuration aggregating all sub-configs."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Sub-configurations
    wallet: WalletConfig = Field(default_factory=WalletConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    market_filter: MarketFilterConfig = Field(default_factory=MarketFilterConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)

    # Bot operation mode
    dry_run: bool = Field(
        default=True,
        description="Run in simulation mode without real trades"
    )

    def validate_config(self) -> bool:
        """Validate configuration before starting bot."""
        errors = []

        if not self.dry_run:
            if not self.wallet.private_key:
                errors.append("Private key required for live trading")
            if not self.wallet.wallet_address:
                errors.append("Wallet address required for live trading")

        if self.trading.max_position_size > self.trading.total_capital:
            errors.append("Max position size cannot exceed total capital")

        if errors:
            for error in errors:
                print(f"Config Error: {error}")
            return False

        return True


# Global config instance
def get_config() -> BotConfig:
    """Get or create global configuration instance."""
    return BotConfig()
