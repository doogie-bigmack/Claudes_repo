# =============================================================================
# Polymarket Arbitrage Bot - Makefile
# =============================================================================
# Convenient commands for Docker and development
# =============================================================================

.PHONY: help build run run-live stop logs shell test clean prune

# Default target
help:
	@echo "Polymarket Arbitrage Bot - Available Commands"
	@echo "=============================================="
	@echo ""
	@echo "Docker Commands:"
	@echo "  make build       Build Docker image"
	@echo "  make run         Run bot in dry-run mode"
	@echo "  make run-live    Run bot in live trading mode (CAUTION!)"
	@echo "  make stop        Stop running containers"
	@echo "  make logs        View container logs"
	@echo "  make shell       Open shell in container"
	@echo "  make test        Run tests in container"
	@echo ""
	@echo "Development:"
	@echo "  make dev         Run locally (not in Docker)"
	@echo "  make test-local  Run tests locally"
	@echo "  make lint        Run linter"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean       Remove containers and images"
	@echo "  make prune       Docker system prune"
	@echo ""

# =============================================================================
# Docker Commands
# =============================================================================

# Build the Docker image
build:
	@echo "Building Docker image..."
	docker build -t polymarket-arb:latest .
	@echo "Done! Image size:"
	@docker images polymarket-arb:latest --format "{{.Size}}"

# Build with no cache
build-fresh:
	docker build --no-cache -t polymarket-arb:latest .

# Run in dry-run mode (default, safe)
run:
	@echo "Starting bot in DRY RUN mode..."
	docker-compose up -d bot
	@echo "Bot started. View logs with: make logs"

# Run in live trading mode (CAUTION!)
run-live:
	@echo "=========================================="
	@echo "WARNING: Starting LIVE TRADING mode!"
	@echo "This will use real funds!"
	@echo "=========================================="
	@read -p "Are you sure? (yes/no): " confirm && [ "$$confirm" = "yes" ]
	docker-compose --profile live up -d bot-live
	@echo "Live bot started. View logs with: make logs-live"

# Run interactively (foreground)
run-interactive:
	docker-compose up bot

# Stop all containers
stop:
	docker-compose down

# View logs (dry-run bot)
logs:
	docker-compose logs -f bot

# View logs (live bot)
logs-live:
	docker-compose logs -f bot-live

# Tail last 100 lines
logs-tail:
	docker-compose logs --tail=100 bot

# Open shell in running container
shell:
	docker-compose exec bot sh

# Run shell in new container
shell-new:
	docker run -it --rm polymarket-arb:latest sh

# Run tests
test:
	docker-compose --profile test run --rm test

# Check container status
status:
	@docker-compose ps
	@echo ""
	@echo "Container stats:"
	@docker stats --no-stream $$(docker-compose ps -q) 2>/dev/null || echo "No running containers"

# =============================================================================
# Development Commands (Local, not Docker)
# =============================================================================

# Run locally
dev:
	python -m src.main run --dry-run --dashboard

# Run tests locally
test-local:
	pytest tests/ -v

# Run linter
lint:
	black src/ tests/ --check
	isort src/ tests/ --check

# Format code
format:
	black src/ tests/
	isort src/ tests/

# Type check
typecheck:
	mypy src/

# =============================================================================
# Cleanup Commands
# =============================================================================

# Remove containers and local image
clean:
	docker-compose down --rmi local --volumes --remove-orphans
	rm -rf logs/*.log

# Deep clean - remove all related Docker resources
clean-all:
	docker-compose down --rmi all --volumes --remove-orphans
	docker image prune -f

# Docker system prune
prune:
	docker system prune -f

# =============================================================================
# Utility Commands
# =============================================================================

# Show image size
size:
	@docker images polymarket-arb:latest --format "Image: {{.Repository}}:{{.Tag}}\nSize: {{.Size}}\nCreated: {{.CreatedSince}}"

# Test API connection
test-connection:
	docker run --rm polymarket-arb:latest test-connection

# Show environment
env:
	@cat .env.example
