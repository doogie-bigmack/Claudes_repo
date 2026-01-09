#!/bin/sh
# =============================================================================
# Docker Entrypoint Script for Polymarket Arbitrage Bot
# =============================================================================

set -e

# Display banner
echo "=============================================="
echo "  Polymarket Arbitrage Trading Bot"
echo "=============================================="
echo "  Mode: ${DRY_RUN:-true}"
echo "  Log Level: ${LOG_LEVEL:-INFO}"
echo "=============================================="

# Check if .env file exists (mounted as volume)
if [ -f "/app/.env" ]; then
    echo "Found .env configuration file"
    export $(grep -v '^#' /app/.env | xargs -0)
fi

# Validate required environment variables for live trading
if [ "$DRY_RUN" = "false" ] || [ "$1" = "--live" ]; then
    if [ -z "$PRIVATE_KEY" ]; then
        echo "ERROR: PRIVATE_KEY is required for live trading"
        echo "Set it via environment variable or mount .env file"
        exit 1
    fi
    if [ -z "$WALLET_ADDRESS" ]; then
        echo "ERROR: WALLET_ADDRESS is required for live trading"
        exit 1
    fi
    echo "Live trading mode - wallet configured"
fi

# Handle different commands
case "$1" in
    run)
        shift
        echo "Starting bot..."
        exec python -m src.main run "$@"
        ;;
    test)
        echo "Running tests..."
        exec python -m pytest tests/ -v
        ;;
    test-connection)
        echo "Testing API connection..."
        exec python -m src.main test-connection
        ;;
    status)
        echo "Checking status..."
        exec python -m src.main status
        ;;
    shell)
        echo "Starting Python shell..."
        exec python
        ;;
    bash|sh)
        echo "Starting shell..."
        exec /bin/sh
        ;;
    help|--help|-h)
        echo ""
        echo "Usage: docker run polymarket-arb [COMMAND] [OPTIONS]"
        echo ""
        echo "Commands:"
        echo "  run             Start the trading bot (default)"
        echo "  test            Run unit tests"
        echo "  test-connection Test API connectivity"
        echo "  status          Show bot status"
        echo "  shell           Start Python shell"
        echo "  sh              Start system shell"
        echo ""
        echo "Run Options:"
        echo "  --dry-run       Run in simulation mode (default)"
        echo "  --live          Run with real trading (CAUTION!)"
        echo "  --capital N     Set starting capital"
        echo "  --dashboard     Enable live terminal dashboard"
        echo ""
        echo "Environment Variables:"
        echo "  PRIVATE_KEY     Polygon wallet private key (required for live)"
        echo "  WALLET_ADDRESS  Wallet address"
        echo "  TOTAL_CAPITAL   Trading capital in USDC"
        echo "  DRY_RUN         true/false (default: true)"
        echo "  LOG_LEVEL       DEBUG/INFO/WARNING/ERROR"
        echo ""
        exit 0
        ;;
    *)
        # If no recognized command, pass everything to the bot
        exec python -m src.main "$@"
        ;;
esac
