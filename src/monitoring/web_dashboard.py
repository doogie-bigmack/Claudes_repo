"""
Web Dashboard for Polymarket Arbitrage Bot

A simple Flask-based web dashboard for monitoring:
- Bot status and health
- Trade history and profits
- Active opportunities
- Risk metrics
- Real-time updates via WebSocket
"""

import asyncio
import json
import threading
from datetime import datetime, timedelta
from typing import Optional

from flask import Flask, jsonify, render_template_string
from flask_socketio import SocketIO

from .metrics import MetricsCollector

# Initialize Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = "polymarket-arb-dashboard"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Global references (set by the bot)
_metrics: Optional[MetricsCollector] = None
_risk_manager = None
_detector = None
_bot_status = {
    "running": False,
    "mode": "dry_run",
    "started_at": None,
    "markets_monitored": 0,
}


def set_dashboard_references(metrics=None, risk_manager=None, detector=None):
    """Set references to bot components for the dashboard."""
    global _metrics, _risk_manager, _detector
    _metrics = metrics
    _risk_manager = risk_manager
    _detector = detector


def update_bot_status(running: bool, mode: str, markets: int):
    """Update bot status for dashboard display."""
    global _bot_status
    _bot_status = {
        "running": running,
        "mode": mode,
        "started_at": datetime.now().isoformat() if running else None,
        "markets_monitored": markets,
    }
    # Emit to connected clients
    socketio.emit("status_update", _bot_status)


# HTML Template with embedded CSS and JavaScript
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Polymarket Arbitrage Bot - Dashboard</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.4/socket.io.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 0;
            border-bottom: 1px solid #333;
            margin-bottom: 30px;
        }

        h1 {
            font-size: 1.8rem;
            color: #00d4ff;
        }

        .status-badge {
            padding: 8px 16px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9rem;
        }

        .status-running {
            background: #00c853;
            color: #000;
        }

        .status-stopped {
            background: #ff5252;
            color: #fff;
        }

        .status-dry-run {
            background: #ffab00;
            color: #000;
        }

        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }

        .card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .card h2 {
            font-size: 1rem;
            color: #888;
            margin-bottom: 15px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .metric-value {
            font-size: 2.5rem;
            font-weight: bold;
            color: #00d4ff;
        }

        .metric-positive {
            color: #00c853;
        }

        .metric-negative {
            color: #ff5252;
        }

        .metric-label {
            font-size: 0.9rem;
            color: #666;
            margin-top: 5px;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
        }

        .stat-item {
            text-align: center;
        }

        .stat-value {
            font-size: 1.5rem;
            font-weight: bold;
            color: #fff;
        }

        .stat-label {
            font-size: 0.8rem;
            color: #666;
        }

        .table-container {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            margin-bottom: 20px;
        }

        .table-container h2 {
            font-size: 1rem;
            color: #888;
            margin-bottom: 15px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }

        th {
            color: #888;
            font-weight: normal;
            font-size: 0.85rem;
            text-transform: uppercase;
        }

        tr:hover {
            background: rgba(255, 255, 255, 0.02);
        }

        .profit {
            color: #00c853;
        }

        .loss {
            color: #ff5252;
        }

        .opportunity-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            background: rgba(0, 212, 255, 0.2);
            color: #00d4ff;
        }

        .progress-bar {
            width: 100%;
            height: 8px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
            overflow: hidden;
            margin-top: 10px;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #00d4ff, #00c853);
            border-radius: 4px;
            transition: width 0.3s ease;
        }

        .risk-item {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .risk-item:last-child {
            border-bottom: none;
        }

        .risk-ok {
            color: #00c853;
        }

        .risk-warning {
            color: #ffab00;
        }

        .risk-danger {
            color: #ff5252;
        }

        .last-update {
            text-align: center;
            color: #666;
            font-size: 0.8rem;
            margin-top: 20px;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .live-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            background: #00c853;
            border-radius: 50%;
            margin-right: 8px;
            animation: pulse 2s infinite;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Polymarket Arbitrage Bot</h1>
            <div>
                <span class="status-badge" id="statusBadge">Connecting...</span>
            </div>
        </header>

        <div class="dashboard-grid">
            <div class="card">
                <h2>Total Profit/Loss</h2>
                <div class="metric-value" id="totalPnL">$0.00</div>
                <div class="metric-label">Since start</div>
            </div>

            <div class="card">
                <h2>Today's P&L</h2>
                <div class="metric-value" id="todayPnL">$0.00</div>
                <div class="metric-label">24h performance</div>
            </div>

            <div class="card">
                <h2>Win Rate</h2>
                <div class="metric-value" id="winRate">0%</div>
                <div class="progress-bar">
                    <div class="progress-fill" id="winRateBar" style="width: 0%"></div>
                </div>
            </div>

            <div class="card">
                <h2>Active Markets</h2>
                <div class="metric-value" id="activeMarkets">0</div>
                <div class="metric-label">Being monitored</div>
            </div>
        </div>

        <div class="dashboard-grid">
            <div class="card">
                <h2>Trading Statistics</h2>
                <div class="stats-grid">
                    <div class="stat-item">
                        <div class="stat-value" id="totalTrades">0</div>
                        <div class="stat-label">Total Trades</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="avgProfit">$0.00</div>
                        <div class="stat-label">Avg Profit/Trade</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="opportunitiesFound">0</div>
                        <div class="stat-label">Opportunities Found</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="avgLatency">0ms</div>
                        <div class="stat-label">Avg Latency</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>Risk Status</h2>
                <div class="risk-item">
                    <span>Daily Loss Limit</span>
                    <span id="dailyLossStatus" class="risk-ok">OK</span>
                </div>
                <div class="risk-item">
                    <span>Position Exposure</span>
                    <span id="positionStatus" class="risk-ok">OK</span>
                </div>
                <div class="risk-item">
                    <span>Trade Rate</span>
                    <span id="tradeRateStatus" class="risk-ok">OK</span>
                </div>
                <div class="risk-item">
                    <span>Circuit Breaker</span>
                    <span id="circuitStatus" class="risk-ok">OK</span>
                </div>
            </div>
        </div>

        <div class="table-container">
            <h2><span class="live-indicator"></span>Active Opportunities</h2>
            <table>
                <thead>
                    <tr>
                        <th>Market</th>
                        <th>YES Price</th>
                        <th>NO Price</th>
                        <th>Spread</th>
                        <th>Est. Profit</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody id="opportunitiesTable">
                    <tr>
                        <td colspan="6" style="text-align: center; color: #666;">
                            No active opportunities
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>

        <div class="table-container">
            <h2>Recent Trades</h2>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Market</th>
                        <th>Size</th>
                        <th>Entry Price</th>
                        <th>P&L</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody id="tradesTable">
                    <tr>
                        <td colspan="6" style="text-align: center; color: #666;">
                            No trades yet
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>

        <div class="last-update">
            Last updated: <span id="lastUpdate">Never</span>
        </div>
    </div>

    <script>
        const socket = io();

        // Update status badge
        function updateStatusBadge(status) {
            const badge = document.getElementById('statusBadge');
            if (status.running) {
                if (status.mode === 'dry_run') {
                    badge.textContent = 'DRY RUN';
                    badge.className = 'status-badge status-dry-run';
                } else {
                    badge.textContent = 'LIVE';
                    badge.className = 'status-badge status-running';
                }
            } else {
                badge.textContent = 'STOPPED';
                badge.className = 'status-badge status-stopped';
            }
            document.getElementById('activeMarkets').textContent = status.markets_monitored || 0;
        }

        // Update metrics display
        function updateMetrics(data) {
            // P&L
            const totalPnL = document.getElementById('totalPnL');
            totalPnL.textContent = '$' + (data.total_pnl || 0).toFixed(2);
            totalPnL.className = 'metric-value ' + (data.total_pnl >= 0 ? 'metric-positive' : 'metric-negative');

            const todayPnL = document.getElementById('todayPnL');
            todayPnL.textContent = '$' + (data.daily_pnl || 0).toFixed(2);
            todayPnL.className = 'metric-value ' + (data.daily_pnl >= 0 ? 'metric-positive' : 'metric-negative');

            // Win rate
            const winRate = data.win_rate || 0;
            document.getElementById('winRate').textContent = winRate.toFixed(1) + '%';
            document.getElementById('winRateBar').style.width = winRate + '%';

            // Stats
            document.getElementById('totalTrades').textContent = data.total_trades || 0;
            document.getElementById('avgProfit').textContent = '$' + (data.avg_profit || 0).toFixed(2);
            document.getElementById('opportunitiesFound').textContent = data.opportunities_found || 0;
            document.getElementById('avgLatency').textContent = (data.avg_latency || 0).toFixed(0) + 'ms';

            // Update timestamp
            document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
        }

        // Update risk status
        function updateRiskStatus(data) {
            const statusMap = {
                'ok': 'risk-ok',
                'warning': 'risk-warning',
                'danger': 'risk-danger'
            };

            ['dailyLoss', 'position', 'tradeRate', 'circuit'].forEach(item => {
                const el = document.getElementById(item + 'Status');
                const status = data[item] || 'ok';
                el.textContent = status.toUpperCase();
                el.className = statusMap[status] || 'risk-ok';
            });
        }

        // Update opportunities table
        function updateOpportunities(opportunities) {
            const tbody = document.getElementById('opportunitiesTable');

            if (!opportunities || opportunities.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #666;">No active opportunities</td></tr>';
                return;
            }

            tbody.innerHTML = opportunities.map(opp => `
                <tr>
                    <td>${opp.market.substring(0, 40)}...</td>
                    <td>$${opp.yes_price.toFixed(2)}</td>
                    <td>$${opp.no_price.toFixed(2)}</td>
                    <td>${(opp.spread * 100).toFixed(2)}%</td>
                    <td class="profit">$${opp.est_profit.toFixed(2)}</td>
                    <td><span class="opportunity-badge">${opp.status}</span></td>
                </tr>
            `).join('');
        }

        // Update trades table
        function updateTrades(trades) {
            const tbody = document.getElementById('tradesTable');

            if (!trades || trades.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #666;">No trades yet</td></tr>';
                return;
            }

            tbody.innerHTML = trades.slice(0, 10).map(trade => `
                <tr>
                    <td>${new Date(trade.timestamp).toLocaleTimeString()}</td>
                    <td>${trade.market.substring(0, 30)}...</td>
                    <td>$${trade.size.toFixed(2)}</td>
                    <td>$${trade.entry_price.toFixed(3)}</td>
                    <td class="${trade.pnl >= 0 ? 'profit' : 'loss'}">$${trade.pnl.toFixed(2)}</td>
                    <td>${trade.status}</td>
                </tr>
            `).join('');
        }

        // Socket event handlers
        socket.on('connect', function() {
            console.log('Connected to dashboard');
            socket.emit('request_update');
        });

        socket.on('status_update', updateStatusBadge);
        socket.on('metrics_update', updateMetrics);
        socket.on('risk_update', updateRiskStatus);
        socket.on('opportunities_update', updateOpportunities);
        socket.on('trades_update', updateTrades);

        socket.on('full_update', function(data) {
            if (data.status) updateStatusBadge(data.status);
            if (data.metrics) updateMetrics(data.metrics);
            if (data.risk) updateRiskStatus(data.risk);
            if (data.opportunities) updateOpportunities(data.opportunities);
            if (data.trades) updateTrades(data.trades);
        });

        // Request updates every 2 seconds
        setInterval(function() {
            socket.emit('request_update');
        }, 2000);
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    """Serve the dashboard HTML."""
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/status")
def api_status():
    """API endpoint for bot status."""
    return jsonify(_bot_status)


@app.route("/api/metrics")
def api_metrics():
    """API endpoint for trading metrics."""
    if not _metrics:
        return jsonify({})

    summary = _metrics.get_summary()
    return jsonify(
        {
            "total_pnl": summary.get("total_pnl", 0),
            "daily_pnl": summary.get("daily_pnl", 0),
            "win_rate": summary.get("win_rate", 0) * 100,
            "total_trades": summary.get("total_trades", 0),
            "avg_profit": summary.get("avg_profit_per_trade", 0),
            "opportunities_found": summary.get("opportunities_detected", 0),
            "avg_latency": summary.get("avg_latency_ms", 0),
        }
    )


@app.route("/api/risk")
def api_risk():
    """API endpoint for risk status."""
    if not _risk_manager:
        return jsonify(
            {"dailyLoss": "ok", "position": "ok", "tradeRate": "ok", "circuit": "ok"}
        )

    status = _risk_manager.get_status()
    risk_level = status.get("risk_level", "low")
    is_halted = status.get("is_halted", False)

    return jsonify(
        {
            "dailyLoss": "danger"
            if is_halted
            else "warning"
            if risk_level in ["medium", "high"]
            else "ok",
            "position": "warning" if risk_level == "high" else "ok",
            "tradeRate": "ok",
            "circuit": "danger" if is_halted else "ok",
        }
    )


@app.route("/api/opportunities")
def api_opportunities():
    """API endpoint for active opportunities."""
    if not _detector:
        return jsonify([])

    opps = _detector.get_opportunities(limit=10)
    return jsonify(
        [
            {
                "market": opp.market_pair.question,
                "yes_price": opp.market_pair.yes_price,
                "no_price": opp.market_pair.no_price,
                "spread": opp.spread,
                "est_profit": opp.estimated_profit,
                "status": opp.status.value if hasattr(opp.status, "value") else "new",
            }
            for opp in opps
        ]
    )


@app.route("/api/trades")
def api_trades():
    """API endpoint for recent trades."""
    if not _metrics:
        return jsonify([])

    trades = _metrics.get_recent_trades(limit=10)
    return jsonify(
        [
            {
                "timestamp": trade.get("timestamp", ""),
                "market": trade.get("market", "Unknown"),
                "size": trade.get("size", 0),
                "entry_price": trade.get("entry_price", 0),
                "pnl": trade.get("pnl", 0),
                "status": trade.get("status", "unknown"),
            }
            for trade in trades
        ]
    )


@socketio.on("connect")
def handle_connect():
    """Handle client connection."""
    # Send initial data
    emit_full_update()


@socketio.on("request_update")
def handle_request_update():
    """Handle update request from client."""
    emit_full_update()


def emit_full_update():
    """Emit full dashboard update to client."""
    data = {
        "status": _bot_status,
        "metrics": {},
        "risk": {"dailyLoss": "ok", "position": "ok", "tradeRate": "ok", "circuit": "ok"},
        "opportunities": [],
        "trades": [],
    }

    if _metrics:
        summary = _metrics.get_summary()
        data["metrics"] = {
            "total_pnl": summary.get("total_pnl", 0),
            "daily_pnl": summary.get("daily_pnl", 0),
            "win_rate": summary.get("win_rate", 0) * 100,
            "total_trades": summary.get("total_trades", 0),
            "avg_profit": summary.get("avg_profit_per_trade", 0),
            "opportunities_found": summary.get("opportunities_detected", 0),
            "avg_latency": summary.get("avg_latency_ms", 0),
        }

        trades = _metrics.get_recent_trades(limit=10)
        data["trades"] = [
            {
                "timestamp": t.get("timestamp", ""),
                "market": t.get("market", "Unknown"),
                "size": t.get("size", 0),
                "entry_price": t.get("entry_price", 0),
                "pnl": t.get("pnl", 0),
                "status": t.get("status", "unknown"),
            }
            for t in trades
        ]

    if _risk_manager:
        status = _risk_manager.get_status()
        risk_level = status.get("risk_level", "low")
        is_halted = status.get("is_halted", False)
        data["risk"] = {
            "dailyLoss": "danger"
            if is_halted
            else "warning"
            if risk_level in ["medium", "high"]
            else "ok",
            "position": "warning" if risk_level == "high" else "ok",
            "tradeRate": "ok",
            "circuit": "danger" if is_halted else "ok",
        }

    if _detector:
        opps = _detector.get_opportunities(limit=10)
        data["opportunities"] = [
            {
                "market": opp.market_pair.question,
                "yes_price": opp.market_pair.yes_price,
                "no_price": opp.market_pair.no_price,
                "spread": opp.spread,
                "est_profit": opp.estimated_profit,
                "status": opp.status.value if hasattr(opp.status, "value") else "new",
            }
            for opp in opps
        ]

    socketio.emit("full_update", data)


def run_dashboard(host: str = "0.0.0.0", port: int = 8080, debug: bool = False):
    """Run the web dashboard server."""
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


def start_dashboard_thread(host: str = "0.0.0.0", port: int = 8080):
    """Start dashboard in a background thread."""
    thread = threading.Thread(
        target=run_dashboard, kwargs={"host": host, "port": port}, daemon=True
    )
    thread.start()
    return thread


# Notify connected clients of updates
def notify_trade(trade_data: dict):
    """Notify clients of a new trade."""
    socketio.emit("trade_update", trade_data)


def notify_opportunity(opportunity_data: dict):
    """Notify clients of a new opportunity."""
    socketio.emit("opportunity_update", opportunity_data)


if __name__ == "__main__":
    # Run standalone for testing
    run_dashboard(debug=True)
