# Polymarket Arbitrage Trading Bot

An automated trading bot for detecting and executing arbitrage opportunities on Polymarket prediction markets.

## Overview

This bot monitors Polymarket binary markets (YES/NO outcomes) and automatically executes trades when it detects arbitrage opportunities - situations where the combined price of YES + NO outcomes is less than $1.00, guaranteeing a profit upon resolution.

### Key Features

- **Real-time Market Monitoring**: Connects to Polymarket's CLOB API for live order book data
- **Arbitrage Detection**: Identifies opportunities where YES + NO < $1 (after fees)
- **Automated Execution**: Places simultaneous orders on both sides
- **Risk Management**: Position limits, daily loss limits, rate limiting, circuit breakers
- **Live Dashboard**: Terminal-based monitoring with real-time metrics
- **Dry Run Mode**: Test strategies without risking capital

## How Arbitrage Works

In a binary prediction market:
- **YES** pays $1 if the event happens
- **NO** pays $1 if the event doesn't happen
- One side **always** pays out

If YES costs $0.48 and NO costs $0.48:
- Total cost: $0.96
- Guaranteed payout: $1.00
- **Gross profit: $0.04 (4.2%)**

However, Polymarket charges fees (up to 3.15% taker fee), so the actual opportunity must exceed fees to be profitable.

## Installation

### Prerequisites

- Python 3.10+
- A Polygon wallet with MATIC (for gas) and USDC (for trading)
- Access to Polymarket (check regional restrictions)

### Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/polymarket-arbitrage-bot.git
cd polymarket-arbitrage-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your wallet details
```

### Configuration

Edit `.env` with your settings:

```env
# Wallet (REQUIRED for live trading)
PRIVATE_KEY=your_polygon_wallet_private_key
WALLET_ADDRESS=0xYourAddress

# Trading parameters
MIN_PROFIT_THRESHOLD=0.005      # 0.5% minimum profit
MAX_POSITION_SIZE=100.0          # Max $100 per trade
TOTAL_CAPITAL=1000.0             # Total capital allocation

# Risk limits
MAX_DAILY_LOSS=0.05              # 5% daily loss limit
TRADE_COOLDOWN=5                 # 5 seconds between trades

# Target markets
TARGET_MARKETS=btc_hourly,eth_hourly
MIN_LIQUIDITY=500                # Minimum $500 liquidity
```

## Usage

### Quick Start (Dry Run)

```bash
# Run in simulation mode (no real trades)
python -m src.main run --dry-run --capital 1000

# With live dashboard
python -m src.main run --dry-run --dashboard
```

### Live Trading

```bash
# WARNING: This uses real money!
python -m src.main run --live --capital 1000
```

### Other Commands

```bash
# Test API connectivity
python -m src.main test-connection

# View status
python -m src.main status
```

## Architecture

```
polymarket-arbitrage-bot/
├── config/
│   └── settings.py          # Configuration management
├── src/
│   ├── api/
│   │   ├── gamma_client.py  # Market data API
│   │   ├── clob_client.py   # Order book & trading API
│   │   └── websocket_client.py  # Real-time price feeds
│   ├── arbitrage/
│   │   ├── detector.py      # Opportunity detection
│   │   ├── calculator.py    # Profit calculations
│   │   └── strategies.py    # Trading strategies
│   ├── execution/
│   │   ├── wallet.py        # Polygon wallet management
│   │   └── order_manager.py # Order execution
│   ├── risk/
│   │   └── risk_manager.py  # Risk controls
│   ├── monitoring/
│   │   ├── logger.py        # Logging setup
│   │   ├── metrics.py       # Performance tracking
│   │   └── dashboard.py     # Terminal dashboard
│   └── main.py              # Bot orchestrator
└── tests/
    └── test_arbitrage.py    # Unit tests
```

## Trading Strategies

### 1. Intra-Market Arbitrage (Default)

Buys both YES and NO on the same market when combined price < $1.

**Best for:**
- Short-term markets (hourly, 15-minute)
- High liquidity markets
- Volatile periods with price inefficiencies

### 2. Cross-Platform Arbitrage

Exploits price differences between Polymarket and Kalshi (or other platforms).

**Requirements:**
- Accounts on multiple platforms
- Capital on each platform
- Lower fees (no gas on Kalshi)

### 3. Multi-Outcome Arbitrage

For markets with >2 outcomes where sum of all prices < $1.

## Risk Management

The bot includes comprehensive risk controls:

| Control | Default | Description |
|---------|---------|-------------|
| Max Position Size | $100 | Per-trade limit |
| Daily Loss Limit | 5% | Stops trading after loss |
| Max Trades/Hour | 100 | Rate limiting |
| Trade Cooldown | 5s | Minimum between trades |
| Consecutive Losses | 5 | Halts after 5 losses |
| Drawdown Circuit Breaker | 10% | Emergency stop |

## Fee Considerations

Polymarket's fee structure (as of January 2026):

| Fee Type | Rate | Notes |
|----------|------|-------|
| Taker Fee | Up to 3.15% | Dynamic, higher near 50/50 |
| Maker Rebate | ~0.5% | For limit orders |
| Gas | ~$0.01-0.10 | Polygon network fees |

**Minimum Required Spread:**

With 3.15% taker fees on both sides:
- Minimum spread needed: ~6.5%
- Maximum combined price: ~$0.935

## Expected Returns

**Realistic expectations:**

- Opportunities are rare and competitive
- Margins are slim (0.5-3% when they exist)
- High volume of small profits
- Bots compete for the same opportunities

**Historical examples:**
- Some bots turned $300 into $400k+ over time
- Others lose money to fees and failed executions
- Most profit comes from high trade volume, not large margins

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific tests
pytest tests/test_arbitrage.py -v

# With coverage
pytest tests/ --cov=src --cov-report=html
```

## Monitoring

The bot provides real-time monitoring through:

1. **Terminal Dashboard**: Live view of opportunities, trades, and metrics
2. **Log Files**: Detailed logs in `logs/` directory
3. **Trade Records**: Separate trade log for analysis

### Metrics Tracked

- Win rate
- Total profit/loss
- Average profit per trade
- Execution latency
- Opportunity detection rate
- Sharpe ratio

## Troubleshooting

### Common Issues

**"Insufficient capital"**
- Ensure USDC is available in your wallet
- Check total exposure isn't at limit

**"Trade blocked by risk manager"**
- Check daily loss limits
- Verify trade cooldown
- Review consecutive losses

**"No markets found"**
- Verify API connectivity
- Check market filters in config
- Ensure markets are active

**"Approval failed"**
- Ensure enough MATIC for gas
- Check wallet configuration

### Logs

Check logs for detailed information:
```bash
# Main bot logs
tail -f logs/bot_$(date +%Y-%m-%d).log

# Error logs
tail -f logs/errors_$(date +%Y-%m-%d).log

# Trade logs
tail -f logs/trades_$(date +%Y-%m-%d).log
```

## Disclaimer

**This software is provided for educational and research purposes only.**

- **High Risk**: Automated trading carries significant financial risk
- **No Guarantees**: Past performance does not indicate future results
- **Fees Change**: Platform fees can change and eliminate profitability
- **Regional Restrictions**: Polymarket may not be available in your region
- **Not Financial Advice**: This is not investment or financial advice

**Use at your own risk. Only trade with funds you can afford to lose.**

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

## License

MIT License - see LICENSE file for details.

## Resources

- [Polymarket Documentation](https://docs.polymarket.com)
- [Polymarket API](https://gamma-api.polymarket.com)
- [py-clob-client](https://github.com/polymarket/py-clob-client)
- [Polygon Network](https://polygon.technology)

## Support

For issues and feature requests, please open a GitHub issue.
