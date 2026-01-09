"""
WebSocket Client for real-time Polymarket data.

Provides low-latency streaming of:
- Order book updates
- Trade executions
- Price changes

Endpoint: wss://ws-subscriptions-clob.polymarket.com/ws
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

import websockets
from loguru import logger
from websockets.client import WebSocketClientProtocol


class ChannelType(str, Enum):
    """WebSocket channel types."""

    MARKET = "market"
    USER = "user"


class MessageType(str, Enum):
    """WebSocket message types."""

    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    BOOK = "book"
    PRICE_CHANGE = "price_change"
    TRADE = "trade"
    LAST_TRADE_PRICE = "last_trade_price"


@dataclass
class PriceUpdate:
    """Price update from WebSocket."""

    token_id: str
    price: float
    side: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class BookUpdate:
    """Order book update from WebSocket."""

    token_id: str
    bids: List[Dict[str, float]] = field(default_factory=list)
    asks: List[Dict[str, float]] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TradeUpdate:
    """Trade notification from WebSocket."""

    token_id: str
    price: float
    size: float
    side: str
    timestamp: datetime = field(default_factory=datetime.now)


# Type alias for callbacks
PriceCallback = Callable[[PriceUpdate], None]
BookCallback = Callable[[BookUpdate], None]
TradeCallback = Callable[[TradeUpdate], None]


class WebSocketClient:
    """
    WebSocket client for real-time Polymarket updates.

    Example usage:
        client = WebSocketClient()

        def on_price(update: PriceUpdate):
            print(f"Price: {update.token_id} = {update.price}")

        client.on_price_update(on_price)

        await client.connect()
        await client.subscribe_market(token_id)

        # Keep running
        await client.run_forever()
    """

    def __init__(
        self,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 10,
        ping_interval: float = 30.0,
    ):
        self.ws_url = ws_url
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts
        self.ping_interval = ping_interval

        self._ws: Optional[WebSocketClientProtocol] = None
        self._connected = False
        self._running = False
        self._reconnect_count = 0

        # Subscriptions
        self._subscribed_tokens: Set[str] = set()

        # Callbacks
        self._price_callbacks: List[PriceCallback] = []
        self._book_callbacks: List[BookCallback] = []
        self._trade_callbacks: List[TradeCallback] = []

        # Message queues for async processing
        self._message_queue: asyncio.Queue = asyncio.Queue()

        # Latest prices cache
        self._prices: Dict[str, float] = {}

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected and self._ws is not None

    @property
    def prices(self) -> Dict[str, float]:
        """Get cached prices."""
        return self._prices.copy()

    def on_price_update(self, callback: PriceCallback):
        """Register a price update callback."""
        self._price_callbacks.append(callback)

    def on_book_update(self, callback: BookCallback):
        """Register a book update callback."""
        self._book_callbacks.append(callback)

    def on_trade_update(self, callback: TradeCallback):
        """Register a trade update callback."""
        self._trade_callbacks.append(callback)

    async def connect(self) -> bool:
        """
        Establish WebSocket connection.

        Returns:
            True if connected successfully
        """
        try:
            logger.info(f"Connecting to WebSocket: {self.ws_url}")
            self._ws = await websockets.connect(
                self.ws_url,
                ping_interval=self.ping_interval,
                ping_timeout=10,
            )
            self._connected = True
            self._reconnect_count = 0
            logger.info("WebSocket connected")

            # Resubscribe to previous tokens
            if self._subscribed_tokens:
                await self._resubscribe()

            return True
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected = False
        logger.info("WebSocket disconnected")

    async def _resubscribe(self):
        """Resubscribe to all previous subscriptions."""
        for token_id in self._subscribed_tokens.copy():
            await self._send_subscribe(token_id)

    async def _send_subscribe(self, token_id: str):
        """Send subscription message."""
        if not self._ws:
            return

        message = {
            "type": MessageType.SUBSCRIBE.value,
            "channel": ChannelType.MARKET.value,
            "assets_ids": [token_id],
        }

        try:
            await self._ws.send(json.dumps(message))
            logger.debug(f"Subscribed to {token_id}")
        except Exception as e:
            logger.error(f"Failed to subscribe: {e}")

    async def subscribe_market(self, token_id: str):
        """
        Subscribe to market updates for a token.

        Args:
            token_id: The token ID to subscribe to
        """
        self._subscribed_tokens.add(token_id)
        if self.is_connected:
            await self._send_subscribe(token_id)

    async def subscribe_markets(self, token_ids: List[str]):
        """
        Subscribe to multiple markets.

        Args:
            token_ids: List of token IDs
        """
        for token_id in token_ids:
            await self.subscribe_market(token_id)

    async def unsubscribe_market(self, token_id: str):
        """
        Unsubscribe from market updates.

        Args:
            token_id: The token ID to unsubscribe from
        """
        self._subscribed_tokens.discard(token_id)

        if not self._ws:
            return

        message = {
            "type": MessageType.UNSUBSCRIBE.value,
            "channel": ChannelType.MARKET.value,
            "assets_ids": [token_id],
        }

        try:
            await self._ws.send(json.dumps(message))
            logger.debug(f"Unsubscribed from {token_id}")
        except Exception as e:
            logger.error(f"Failed to unsubscribe: {e}")

    def _process_message(self, data: Dict[str, Any]):
        """Process incoming WebSocket message."""
        msg_type = data.get("type", data.get("event_type", ""))

        if msg_type in ("price_change", "last_trade_price"):
            # Price update
            token_id = data.get("asset_id", data.get("token_id", ""))
            price = float(data.get("price", 0))

            if token_id:
                self._prices[token_id] = price
                update = PriceUpdate(
                    token_id=token_id,
                    price=price,
                    side=data.get("side", ""),
                )
                for callback in self._price_callbacks:
                    try:
                        callback(update)
                    except Exception as e:
                        logger.error(f"Price callback error: {e}")

        elif msg_type == "book":
            # Order book update
            token_id = data.get("asset_id", data.get("token_id", ""))
            if token_id:
                update = BookUpdate(
                    token_id=token_id,
                    bids=data.get("bids", []),
                    asks=data.get("asks", []),
                )
                for callback in self._book_callbacks:
                    try:
                        callback(update)
                    except Exception as e:
                        logger.error(f"Book callback error: {e}")

        elif msg_type == "trade":
            # Trade notification
            token_id = data.get("asset_id", data.get("token_id", ""))
            if token_id:
                update = TradeUpdate(
                    token_id=token_id,
                    price=float(data.get("price", 0)),
                    size=float(data.get("size", 0)),
                    side=data.get("side", ""),
                )
                for callback in self._trade_callbacks:
                    try:
                        callback(update)
                    except Exception as e:
                        logger.error(f"Trade callback error: {e}")

    async def _handle_messages(self):
        """Handle incoming WebSocket messages."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)

                    # Handle array of messages
                    if isinstance(data, list):
                        for item in data:
                            self._process_message(item)
                    else:
                        self._process_message(data)

                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON received: {e}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")

        except websockets.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}")
            self._connected = False
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            self._connected = False

    async def _reconnect_loop(self):
        """Handle automatic reconnection."""
        while self._running:
            if not self._connected:
                if self._reconnect_count >= self.max_reconnect_attempts:
                    logger.error("Max reconnection attempts reached")
                    break

                self._reconnect_count += 1
                logger.info(
                    f"Reconnecting (attempt {self._reconnect_count}/"
                    f"{self.max_reconnect_attempts})..."
                )

                await asyncio.sleep(self.reconnect_delay)
                await self.connect()

            await asyncio.sleep(1)

    async def run_forever(self):
        """
        Run the WebSocket client indefinitely.

        Handles message processing and automatic reconnection.
        """
        self._running = True

        # Start reconnection monitor
        reconnect_task = asyncio.create_task(self._reconnect_loop())

        try:
            while self._running:
                if self.is_connected:
                    await self._handle_messages()
                else:
                    await asyncio.sleep(0.1)
        finally:
            self._running = False
            reconnect_task.cancel()
            try:
                await reconnect_task
            except asyncio.CancelledError:
                pass

    async def run_once(self, timeout: float = 5.0) -> List[Dict[str, Any]]:
        """
        Connect, receive messages for a duration, then disconnect.

        Args:
            timeout: How long to listen for messages

        Returns:
            List of received messages
        """
        messages = []

        if not await self.connect():
            return messages

        try:
            end_time = asyncio.get_event_loop().time() + timeout

            while asyncio.get_event_loop().time() < end_time:
                if not self._ws:
                    break

                try:
                    message = await asyncio.wait_for(
                        self._ws.recv(),
                        timeout=min(1.0, end_time - asyncio.get_event_loop().time()),
                    )
                    data = json.loads(message)
                    messages.append(data)
                    self._process_message(data)
                except asyncio.TimeoutError:
                    continue
                except json.JSONDecodeError:
                    continue

        finally:
            await self.disconnect()

        return messages

    def get_price(self, token_id: str) -> Optional[float]:
        """
        Get cached price for a token.

        Args:
            token_id: The token ID

        Returns:
            Cached price or None
        """
        return self._prices.get(token_id)


class MultiMarketWebSocket:
    """
    Manages WebSocket connections for multiple markets.

    Useful for monitoring arbitrage opportunities across many markets.
    """

    def __init__(
        self,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    ):
        self.ws_url = ws_url
        self._client = WebSocketClient(ws_url)

        # Price tracking
        self._yes_prices: Dict[str, float] = {}
        self._no_prices: Dict[str, float] = {}
        self._market_pairs: Dict[str, str] = {}  # yes_token -> no_token

        # Callbacks for arbitrage detection
        self._arb_callbacks: List[Callable] = []

    def register_market_pair(self, yes_token_id: str, no_token_id: str):
        """Register a YES/NO market pair."""
        self._market_pairs[yes_token_id] = no_token_id

    def on_arbitrage_opportunity(self, callback: Callable):
        """Register callback for arbitrage opportunities."""
        self._arb_callbacks.append(callback)

    def _check_arbitrage(self, token_id: str):
        """Check for arbitrage after price update."""
        # Find the paired token
        if token_id in self._market_pairs:
            yes_token = token_id
            no_token = self._market_pairs[yes_token]
        else:
            # Check if this is a NO token
            for yes, no in self._market_pairs.items():
                if no == token_id:
                    yes_token = yes
                    no_token = no
                    break
            else:
                return

        yes_price = self._yes_prices.get(yes_token)
        no_price = self._no_prices.get(no_token)

        if yes_price is not None and no_price is not None:
            combined = yes_price + no_price

            # Check if there's an arbitrage opportunity
            if combined < 1.0:
                profit = 1.0 - combined
                for callback in self._arb_callbacks:
                    try:
                        callback(yes_token, no_token, yes_price, no_price, profit)
                    except Exception as e:
                        logger.error(f"Arb callback error: {e}")

    def _on_price_update(self, update: PriceUpdate):
        """Handle price update."""
        token_id = update.token_id

        # Determine if YES or NO based on our registered pairs
        if token_id in self._market_pairs:
            self._yes_prices[token_id] = update.price
        else:
            for yes, no in self._market_pairs.items():
                if no == token_id:
                    self._no_prices[token_id] = update.price
                    break

        self._check_arbitrage(token_id)

    async def start(self, market_pairs: Dict[str, str]):
        """
        Start monitoring market pairs.

        Args:
            market_pairs: Dict mapping YES token ID to NO token ID
        """
        for yes_token, no_token in market_pairs.items():
            self.register_market_pair(yes_token, no_token)

        self._client.on_price_update(self._on_price_update)

        await self._client.connect()

        # Subscribe to all tokens
        all_tokens = list(market_pairs.keys()) + list(market_pairs.values())
        await self._client.subscribe_markets(all_tokens)

        await self._client.run_forever()

    async def stop(self):
        """Stop monitoring."""
        await self._client.disconnect()


async def main():
    """Test WebSocket client."""
    client = WebSocketClient()

    def on_price(update: PriceUpdate):
        logger.info(f"Price update: {update.token_id} = {update.price}")

    client.on_price_update(on_price)

    logger.info("Starting WebSocket client...")

    # Would need real token IDs for actual testing
    # await client.subscribe_market("token_id_here")
    # await client.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
