"""
CLOB (Central Limit Order Book) API Client for Polymarket.

The CLOB API provides:
- Real-time order book data
- Order placement and management
- Trade execution
- Account balances and positions

Endpoint: https://clob.polymarket.com
"""

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from loguru import logger


class OrderSide(str, Enum):
    """Order side enumeration."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type enumeration."""
    LIMIT = "GTC"  # Good-til-canceled
    FOK = "FOK"    # Fill-or-kill
    IOC = "IOC"    # Immediate-or-cancel


class OrderStatus(str, Enum):
    """Order status enumeration."""
    LIVE = "LIVE"
    MATCHED = "MATCHED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass
class OrderBookLevel:
    """Single level in order book."""
    price: float
    size: float

    @property
    def price_decimal(self) -> Decimal:
        return Decimal(str(self.price))

    @property
    def size_decimal(self) -> Decimal:
        return Decimal(str(self.size))


@dataclass
class OrderBook:
    """Order book for a token."""
    token_id: str
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    timestamp: Optional[datetime] = None

    @property
    def best_bid(self) -> Optional[float]:
        """Best (highest) bid price."""
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        """Best (lowest) ask price."""
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        """Bid-ask spread."""
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """Mid-market price."""
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None


@dataclass
class Order:
    """Represents an order."""
    order_id: str
    token_id: str
    side: OrderSide
    price: float
    size: float
    size_matched: float = 0.0
    status: OrderStatus = OrderStatus.LIVE
    created_at: Optional[datetime] = None
    order_type: OrderType = OrderType.LIMIT

    @property
    def remaining_size(self) -> float:
        return self.size - self.size_matched

    @property
    def is_filled(self) -> bool:
        return self.size_matched >= self.size


@dataclass
class Trade:
    """Represents a completed trade."""
    trade_id: str
    token_id: str
    side: OrderSide
    price: float
    size: float
    fee: float = 0.0
    timestamp: Optional[datetime] = None


@dataclass
class Position:
    """Represents a position in a token."""
    token_id: str
    size: float
    average_price: float
    realized_pnl: float = 0.0

    @property
    def market_value(self) -> float:
        """Notional value at average price."""
        return self.size * self.average_price


class CLOBClient:
    """
    Client for Polymarket's CLOB API.

    Handles authentication, order management, and trade execution.

    Example usage:
        client = CLOBClient(
            private_key="your_private_key",
            wallet_address="0x..."
        )
        async with client:
            # Get order book
            book = await client.get_order_book(token_id)

            # Place order
            order = await client.place_order(
                token_id=token_id,
                side=OrderSide.BUY,
                price=0.50,
                size=10.0,
            )
    """

    def __init__(
        self,
        private_key: str = "",
        wallet_address: str = "",
        base_url: str = "https://clob.polymarket.com",
        chain_id: int = 137,
        timeout: float = 30.0,
    ):
        self.private_key = private_key
        self.wallet_address = wallet_address
        self.base_url = base_url.rstrip("/")
        self.chain_id = chain_id
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._api_key: Optional[str] = None
        self._api_secret: Optional[str] = None
        self._api_passphrase: Optional[str] = None

    async def __aenter__(self) -> "CLOBClient":
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
        )
        # Derive API credentials if we have a private key
        if self.private_key:
            await self._derive_api_credentials()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        """Get HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _sign_message(self, message: str) -> str:
        """Sign a message with the private key."""
        if not self.private_key:
            raise ValueError("Private key required for signing")

        account = Account.from_key(self.private_key)
        message_hash = encode_defunct(text=message)
        signed = account.sign_message(message_hash)
        return signed.signature.hex()

    async def _derive_api_credentials(self):
        """
        Derive API credentials from private key.

        Polymarket uses a derived API key system where credentials
        are generated from signing a specific message.
        """
        if not self.private_key:
            return

        # Generate nonce
        nonce = int(time.time() * 1000)

        # Create the message to sign
        message = f"Sign in to Polymarket\nTimestamp: {nonce}"

        # Sign the message
        signature = self._sign_message(message)

        # Request API credentials
        try:
            response = await self.client.post(
                "/auth/derive-api-key",
                json={
                    "message": message,
                    "signature": signature,
                    "timestamp": nonce,
                },
            )

            if response.status_code == 200:
                data = response.json()
                self._api_key = data.get("apiKey")
                self._api_secret = data.get("secret")
                self._api_passphrase = data.get("passphrase")
                logger.info("API credentials derived successfully")
            else:
                logger.warning(f"Failed to derive API credentials: {response.text}")
        except Exception as e:
            logger.warning(f"Error deriving API credentials: {e}")

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for API requests."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self._api_key:
            timestamp = str(int(time.time() * 1000))
            headers.update({
                "POLY_API_KEY": self._api_key,
                "POLY_TIMESTAMP": timestamp,
                "POLY_PASSPHRASE": self._api_passphrase or "",
            })

            # Generate signature if we have a secret
            if self._api_secret:
                message = timestamp + "GET" + "/"
                signature = hmac.new(
                    self._api_secret.encode(),
                    message.encode(),
                    hashlib.sha256,
                ).hexdigest()
                headers["POLY_SIGNATURE"] = signature

        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        authenticated: bool = False,
    ) -> Dict[str, Any]:
        """Make an API request."""
        headers = self._get_auth_headers() if authenticated else {
            "Accept": "application/json",
        }

        try:
            response = await self.client.request(
                method,
                endpoint,
                params=params,
                json=json_data,
                headers=headers,
            )
            response.raise_for_status()

            if response.text:
                return response.json()
            return {}
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            raise

    async def get_order_book(
        self,
        token_id: str,
        depth: int = 20,
    ) -> OrderBook:
        """
        Get order book for a token.

        Args:
            token_id: The token ID
            depth: Number of levels to fetch

        Returns:
            OrderBook with bids and asks
        """
        data = await self._request(
            "GET",
            f"/book",
            params={"token_id": token_id},
        )

        bids = [
            OrderBookLevel(
                price=float(level.get("price", 0)),
                size=float(level.get("size", 0)),
            )
            for level in data.get("bids", [])[:depth]
        ]

        asks = [
            OrderBookLevel(
                price=float(level.get("price", 0)),
                size=float(level.get("size", 0)),
            )
            for level in data.get("asks", [])[:depth]
        ]

        return OrderBook(
            token_id=token_id,
            bids=sorted(bids, key=lambda x: x.price, reverse=True),
            asks=sorted(asks, key=lambda x: x.price),
            timestamp=datetime.now(),
        )

    async def get_order_books(
        self,
        token_ids: List[str],
    ) -> Dict[str, OrderBook]:
        """
        Get order books for multiple tokens.

        Args:
            token_ids: List of token IDs

        Returns:
            Dictionary mapping token_id to OrderBook
        """
        tasks = [self.get_order_book(tid) for tid in token_ids]
        books = await asyncio.gather(*tasks, return_exceptions=True)

        result = {}
        for token_id, book in zip(token_ids, books):
            if isinstance(book, OrderBook):
                result[token_id] = book
            else:
                logger.warning(f"Failed to get order book for {token_id}: {book}")

        return result

    async def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get mid-market price for a token."""
        try:
            data = await self._request(
                "GET",
                "/midpoint",
                params={"token_id": token_id},
            )
            return float(data.get("mid", 0))
        except Exception:
            return None

    async def get_price(self, token_id: str, side: OrderSide) -> Optional[float]:
        """
        Get best price for a token on given side.

        Args:
            token_id: The token ID
            side: BUY or SELL

        Returns:
            Best available price
        """
        try:
            data = await self._request(
                "GET",
                "/price",
                params={
                    "token_id": token_id,
                    "side": side.value,
                },
            )
            return float(data.get("price", 0))
        except Exception:
            return None

    async def get_prices(
        self,
        token_ids: List[str],
    ) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
        """
        Get bid/ask prices for multiple tokens.

        Returns:
            Dictionary mapping token_id to (bid, ask) tuple
        """
        async def get_both(token_id: str):
            bid = await self.get_price(token_id, OrderSide.BUY)
            ask = await self.get_price(token_id, OrderSide.SELL)
            return token_id, (bid, ask)

        results = await asyncio.gather(*[get_both(tid) for tid in token_ids])
        return dict(results)

    async def place_order(
        self,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
        order_type: OrderType = OrderType.LIMIT,
    ) -> Optional[Order]:
        """
        Place an order.

        Args:
            token_id: The token to trade
            side: BUY or SELL
            price: Limit price (0-1)
            size: Number of contracts
            order_type: Order type (GTC, FOK, IOC)

        Returns:
            Order object if successful
        """
        if not self.private_key:
            logger.error("Cannot place order: no private key configured")
            return None

        # Build order payload
        order_payload = {
            "tokenID": token_id,
            "price": str(price),
            "size": str(size),
            "side": side.value,
            "type": order_type.value,
        }

        # Sign the order
        order_json = json.dumps(order_payload, separators=(",", ":"), sort_keys=True)
        signature = self._sign_message(order_json)

        order_payload["signature"] = signature

        try:
            data = await self._request(
                "POST",
                "/order",
                json_data=order_payload,
                authenticated=True,
            )

            return Order(
                order_id=data.get("orderID", data.get("id", "")),
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                status=OrderStatus.LIVE,
                created_at=datetime.now(),
                order_type=order_type,
            )
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            order_id: The order ID to cancel

        Returns:
            True if cancelled successfully
        """
        try:
            await self._request(
                "DELETE",
                f"/order/{order_id}",
                authenticated=True,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def cancel_all_orders(self) -> int:
        """
        Cancel all open orders.

        Returns:
            Number of orders cancelled
        """
        try:
            data = await self._request(
                "DELETE",
                "/orders",
                authenticated=True,
            )
            cancelled = data.get("cancelled", 0)
            logger.info(f"Cancelled {cancelled} orders")
            return cancelled
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")
            return 0

    async def get_open_orders(self) -> List[Order]:
        """Get all open orders."""
        try:
            data = await self._request(
                "GET",
                "/orders",
                authenticated=True,
            )

            orders = []
            for item in data if isinstance(data, list) else data.get("orders", []):
                order = Order(
                    order_id=item.get("id", ""),
                    token_id=item.get("tokenID", item.get("token_id", "")),
                    side=OrderSide(item.get("side", "BUY")),
                    price=float(item.get("price", 0)),
                    size=float(item.get("originalSize", item.get("size", 0))),
                    size_matched=float(item.get("sizeMatched", 0)),
                    status=OrderStatus(item.get("status", "LIVE")),
                )
                orders.append(order)

            return orders
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []

    async def get_trades(
        self,
        limit: int = 100,
    ) -> List[Trade]:
        """Get recent trades for the account."""
        try:
            data = await self._request(
                "GET",
                "/trades",
                params={"limit": limit},
                authenticated=True,
            )

            trades = []
            for item in data if isinstance(data, list) else data.get("trades", []):
                trade = Trade(
                    trade_id=item.get("id", ""),
                    token_id=item.get("tokenID", item.get("token_id", "")),
                    side=OrderSide(item.get("side", "BUY")),
                    price=float(item.get("price", 0)),
                    size=float(item.get("size", 0)),
                    fee=float(item.get("fee", 0)),
                )
                trades.append(trade)

            return trades
        except Exception as e:
            logger.error(f"Failed to get trades: {e}")
            return []

    async def get_balance(self) -> Dict[str, float]:
        """Get account balances."""
        try:
            data = await self._request(
                "GET",
                "/balance",
                authenticated=True,
            )
            return {
                "usdc": float(data.get("usdc", 0)),
                "collateral": float(data.get("collateral", 0)),
            }
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return {"usdc": 0, "collateral": 0}


async def main():
    """Test the CLOB client."""
    # Test without authentication (read-only)
    async with CLOBClient() as client:
        # You would need a real token ID here
        token_id = "example_token_id"

        logger.info(f"Testing order book fetch for {token_id}...")
        # This would fail without a real token ID
        # book = await client.get_order_book(token_id)


if __name__ == "__main__":
    asyncio.run(main())
