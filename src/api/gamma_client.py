"""
Gamma API Client for Polymarket.

The Gamma API provides read-only access to market data, including:
- Market listings and metadata
- Current prices and volumes
- Historical data
- Market resolution status

Endpoint: https://gamma-api.polymarket.com
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

import httpx
from loguru import logger


class MarketStatus(str, Enum):
    """Market status enumeration."""
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


@dataclass
class MarketOutcome:
    """Represents a market outcome (YES/NO side)."""
    token_id: str
    outcome: str  # "Yes" or "No"
    price: float
    volume: float = 0.0

    @property
    def price_cents(self) -> int:
        """Price in cents (0-100)."""
        return int(self.price * 100)


@dataclass
class Market:
    """Represents a Polymarket market."""
    condition_id: str
    question_id: str
    question: str
    description: str
    end_date: Optional[datetime]
    outcomes: List[MarketOutcome] = field(default_factory=list)
    volume: float = 0.0
    liquidity: float = 0.0
    status: MarketStatus = MarketStatus.UNKNOWN
    category: str = ""
    tags: List[str] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def yes_price(self) -> Optional[float]:
        """Get YES outcome price."""
        for outcome in self.outcomes:
            if outcome.outcome.lower() == "yes":
                return outcome.price
        return None

    @property
    def no_price(self) -> Optional[float]:
        """Get NO outcome price."""
        for outcome in self.outcomes:
            if outcome.outcome.lower() == "no":
                return outcome.price
        return None

    @property
    def combined_price(self) -> Optional[float]:
        """Sum of YES + NO prices (should be ~1.0 for efficient markets)."""
        yes = self.yes_price
        no = self.no_price
        if yes is not None and no is not None:
            return yes + no
        return None

    @property
    def is_binary(self) -> bool:
        """Check if this is a binary (YES/NO) market."""
        return len(self.outcomes) == 2

    def is_crypto_short_term(self) -> bool:
        """Check if this is a short-term crypto price market."""
        keywords = ["btc", "bitcoin", "eth", "ethereum", "hourly", "15-min", "15 min"]
        text = f"{self.question} {self.description}".lower()
        return any(kw in text for kw in keywords)


class GammaClient:
    """
    Client for Polymarket's Gamma API (read-only market data).

    Example usage:
        async with GammaClient() as client:
            markets = await client.get_active_markets()
            for market in markets:
                print(f"{market.question}: YES={market.yes_price}, NO={market.no_price}")
    """

    def __init__(
        self,
        base_url: str = "https://gamma-api.polymarket.com",
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "GammaClient":
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        """Get HTTP client, creating if necessary."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make an API request."""
        try:
            response = await self.client.request(
                method,
                endpoint,
                params=params,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            raise

    def _parse_market(self, data: Dict[str, Any]) -> Market:
        """Parse raw market data into Market object."""
        outcomes = []

        # Parse outcomes/tokens
        tokens = data.get("tokens", [])
        for token in tokens:
            outcome = MarketOutcome(
                token_id=token.get("token_id", ""),
                outcome=token.get("outcome", ""),
                price=float(token.get("price", 0) or 0),
                volume=float(token.get("volume", 0) or 0),
            )
            outcomes.append(outcome)

        # Parse end date
        end_date = None
        end_date_str = data.get("end_date_iso") or data.get("end_date")
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(
                    end_date_str.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        # Determine status
        status = MarketStatus.UNKNOWN
        status_str = data.get("active", data.get("closed", ""))
        if data.get("active") is True:
            status = MarketStatus.ACTIVE
        elif data.get("closed") is True:
            status = MarketStatus.CLOSED
        elif data.get("resolved") is True:
            status = MarketStatus.RESOLVED

        return Market(
            condition_id=data.get("condition_id", ""),
            question_id=data.get("question_id", data.get("id", "")),
            question=data.get("question", ""),
            description=data.get("description", ""),
            end_date=end_date,
            outcomes=outcomes,
            volume=float(data.get("volume", 0) or 0),
            liquidity=float(data.get("liquidity", 0) or 0),
            status=status,
            category=data.get("category", ""),
            tags=data.get("tags", []),
            raw_data=data,
        )

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
    ) -> List[Market]:
        """
        Fetch markets from Gamma API.

        Args:
            limit: Maximum number of markets to return
            offset: Pagination offset
            active: Include active markets
            closed: Include closed markets

        Returns:
            List of Market objects
        """
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }

        data = await self._request("GET", "/markets", params=params)

        markets = []
        for item in data if isinstance(data, list) else data.get("data", []):
            try:
                market = self._parse_market(item)
                markets.append(market)
            except Exception as e:
                logger.warning(f"Failed to parse market: {e}")

        return markets

    async def get_active_markets(self, limit: int = 100) -> List[Market]:
        """Get all active markets."""
        return await self.get_markets(limit=limit, active=True, closed=False)

    async def get_market(self, condition_id: str) -> Optional[Market]:
        """
        Fetch a specific market by condition ID.

        Args:
            condition_id: The market's condition ID

        Returns:
            Market object or None if not found
        """
        try:
            data = await self._request("GET", f"/markets/{condition_id}")
            return self._parse_market(data)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_crypto_markets(
        self,
        crypto: str = "btc",
        market_type: str = "hourly",
    ) -> List[Market]:
        """
        Get crypto price prediction markets.

        Args:
            crypto: Cryptocurrency symbol (btc, eth)
            market_type: Market duration (hourly, 15min)

        Returns:
            List of matching crypto markets
        """
        all_markets = await self.get_active_markets(limit=500)

        crypto_lower = crypto.lower()
        type_lower = market_type.lower()

        filtered = []
        for market in all_markets:
            text = f"{market.question} {market.description}".lower()

            # Check for crypto match
            crypto_match = False
            if crypto_lower == "btc":
                crypto_match = "btc" in text or "bitcoin" in text
            elif crypto_lower == "eth":
                crypto_match = "eth" in text or "ethereum" in text
            else:
                crypto_match = crypto_lower in text

            # Check for market type match
            type_match = False
            if type_lower == "hourly":
                type_match = "hourly" in text or "hour" in text
            elif type_lower == "15min":
                type_match = "15-min" in text or "15 min" in text or "15min" in text
            else:
                type_match = type_lower in text

            if crypto_match and type_match:
                filtered.append(market)

        return filtered

    async def get_binary_markets_with_prices(
        self,
        min_liquidity: float = 100.0,
    ) -> List[Market]:
        """
        Get binary markets that have both YES and NO prices.

        Args:
            min_liquidity: Minimum liquidity threshold

        Returns:
            List of binary markets with pricing data
        """
        markets = await self.get_active_markets(limit=500)

        filtered = []
        for market in markets:
            if not market.is_binary:
                continue
            if market.liquidity < min_liquidity:
                continue
            if market.yes_price is None or market.no_price is None:
                continue
            filtered.append(market)

        return filtered

    async def search_markets(self, query: str) -> List[Market]:
        """
        Search markets by keyword.

        Args:
            query: Search query string

        Returns:
            List of matching markets
        """
        all_markets = await self.get_active_markets(limit=500)
        query_lower = query.lower()

        return [
            m for m in all_markets
            if query_lower in m.question.lower()
            or query_lower in m.description.lower()
        ]


async def main():
    """Test the Gamma client."""
    async with GammaClient() as client:
        logger.info("Fetching active markets...")
        markets = await client.get_active_markets(limit=10)

        for market in markets[:5]:
            logger.info(f"Market: {market.question[:50]}...")
            logger.info(f"  YES: {market.yes_price}, NO: {market.no_price}")
            logger.info(f"  Combined: {market.combined_price}")
            logger.info(f"  Liquidity: ${market.liquidity:,.2f}")
            logger.info("")

        # Look for crypto markets
        logger.info("Searching for BTC hourly markets...")
        btc_markets = await client.get_crypto_markets("btc", "hourly")
        logger.info(f"Found {len(btc_markets)} BTC hourly markets")


if __name__ == "__main__":
    asyncio.run(main())
