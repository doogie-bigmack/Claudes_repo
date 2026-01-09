"""
Order management and execution.

Handles:
- Order creation and submission
- Order tracking and status updates
- Fill monitoring
- Position management
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Callable
import uuid

from loguru import logger

from ..api.clob_client import CLOBClient, OrderSide, OrderType, Order, OrderStatus
from .wallet import WalletManager


class ExecutionStatus(str, Enum):
    """Order execution status."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class ExecutionOrder:
    """Internal order tracking."""
    id: str
    token_id: str
    side: OrderSide
    price: float
    size: float
    order_type: OrderType = OrderType.LIMIT

    # Status tracking
    status: ExecutionStatus = ExecutionStatus.PENDING
    filled_size: float = 0.0
    average_fill_price: float = 0.0

    # Exchange order ID
    exchange_order_id: Optional[str] = None

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None

    # Error tracking
    error: Optional[str] = None
    retry_count: int = 0

    @property
    def remaining_size(self) -> float:
        return self.size - self.filled_size

    @property
    def is_complete(self) -> bool:
        return self.status in (
            ExecutionStatus.FILLED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.FAILED,
            ExecutionStatus.EXPIRED,
        )

    @property
    def fill_rate(self) -> float:
        return self.filled_size / self.size if self.size > 0 else 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "token_id": self.token_id[:16] + "...",
            "side": self.side.value,
            "price": f"${self.price:.3f}",
            "size": self.size,
            "filled": self.filled_size,
            "status": self.status.value,
            "fill_rate": f"{self.fill_rate:.0%}",
        }


@dataclass
class ExecutionResult:
    """Result of an order execution."""
    success: bool
    order: ExecutionOrder
    latency_ms: float = 0.0
    slippage: float = 0.0
    error: Optional[str] = None

    @property
    def actual_price(self) -> float:
        return self.order.average_fill_price if self.order.filled_size > 0 else 0


@dataclass
class Position:
    """Trading position in a token."""
    token_id: str
    size: float
    average_price: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def market_value(self) -> float:
        return self.size * self.average_price


class OrderExecutor:
    """
    Executes orders through the CLOB API.

    Example:
        executor = OrderExecutor(clob_client, wallet_manager)

        result = await executor.place_buy_order(
            token_id="...",
            price=0.50,
            size=10,
        )
    """

    def __init__(
        self,
        clob_client: CLOBClient,
        wallet_manager: WalletManager,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        order_timeout: float = 30.0,
    ):
        self.clob = clob_client
        self.wallet = wallet_manager
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.order_timeout = order_timeout

    async def place_buy_order(
        self,
        token_id: str,
        price: float,
        size: float,
        order_type: OrderType = OrderType.LIMIT,
    ) -> ExecutionResult:
        """
        Place a buy order.

        Args:
            token_id: Token to buy
            price: Limit price
            size: Number of contracts
            order_type: Order type

        Returns:
            ExecutionResult
        """
        return await self._execute_order(
            token_id=token_id,
            side=OrderSide.BUY,
            price=price,
            size=size,
            order_type=order_type,
        )

    async def place_sell_order(
        self,
        token_id: str,
        price: float,
        size: float,
        order_type: OrderType = OrderType.LIMIT,
    ) -> ExecutionResult:
        """Place a sell order."""
        return await self._execute_order(
            token_id=token_id,
            side=OrderSide.SELL,
            price=price,
            size=size,
            order_type=order_type,
        )

    async def _execute_order(
        self,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
        order_type: OrderType,
    ) -> ExecutionResult:
        """Execute an order with retries."""
        order = ExecutionOrder(
            id=str(uuid.uuid4()),
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            order_type=order_type,
        )

        start_time = time.time()

        for attempt in range(self.max_retries):
            try:
                order.retry_count = attempt

                # Submit to exchange
                exchange_order = await self.clob.place_order(
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size,
                    order_type=order_type,
                )

                if exchange_order:
                    order.exchange_order_id = exchange_order.order_id
                    order.submitted_at = datetime.now()
                    order.status = ExecutionStatus.SUBMITTED

                    # For immediate-or-cancel, check fill status
                    if order_type == OrderType.IOC:
                        order.filled_size = exchange_order.size_matched
                        order.average_fill_price = price  # Simplified
                        order.status = (
                            ExecutionStatus.FILLED
                            if exchange_order.size_matched >= size
                            else ExecutionStatus.PARTIAL
                        )
                        order.filled_at = datetime.now()

                    latency = (time.time() - start_time) * 1000

                    return ExecutionResult(
                        success=order.status != ExecutionStatus.FAILED,
                        order=order,
                        latency_ms=latency,
                        slippage=0,  # Would calculate from actual fill price
                    )

                # Order failed
                order.status = ExecutionStatus.FAILED
                order.error = "Order submission returned None"

            except Exception as e:
                logger.warning(f"Order attempt {attempt + 1} failed: {e}")
                order.error = str(e)

                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))

        order.status = ExecutionStatus.FAILED
        return ExecutionResult(
            success=False,
            order=order,
            latency_ms=(time.time() - start_time) * 1000,
            error=order.error,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        return await self.clob.cancel_order(order_id)

    async def cancel_all(self) -> int:
        """Cancel all open orders."""
        return await self.clob.cancel_all_orders()


class OrderManager:
    """
    High-level order management.

    Coordinates multiple orders, tracks positions, and manages
    the overall trading state.

    Example:
        manager = OrderManager(clob_client, wallet_manager)

        # Execute arbitrage trade (buy both sides)
        yes_result, no_result = await manager.execute_arbitrage_pair(
            yes_token="...",
            no_token="...",
            yes_price=0.48,
            no_price=0.48,
            size=10,
        )
    """

    def __init__(
        self,
        clob_client: CLOBClient,
        wallet_manager: WalletManager,
        dry_run: bool = True,
    ):
        self.clob = clob_client
        self.wallet = wallet_manager
        self.dry_run = dry_run

        self.executor = OrderExecutor(clob_client, wallet_manager)

        # Order tracking
        self._orders: Dict[str, ExecutionOrder] = {}
        self._positions: Dict[str, Position] = {}

        # Stats
        self._total_orders = 0
        self._successful_orders = 0
        self._failed_orders = 0
        self._total_volume = 0.0

        # Callbacks
        self._order_callbacks: List[Callable] = []

    @property
    def stats(self) -> dict:
        return {
            "total_orders": self._total_orders,
            "successful": self._successful_orders,
            "failed": self._failed_orders,
            "success_rate": (
                f"{self._successful_orders / self._total_orders:.1%}"
                if self._total_orders > 0 else "N/A"
            ),
            "volume": f"${self._total_volume:.2f}",
        }

    def on_order_update(self, callback: Callable):
        """Register callback for order updates."""
        self._order_callbacks.append(callback)

    def _notify_order_update(self, order: ExecutionOrder):
        """Notify callbacks of order update."""
        for callback in self._order_callbacks:
            try:
                callback(order)
            except Exception as e:
                logger.error(f"Order callback error: {e}")

    async def execute_arbitrage_pair(
        self,
        yes_token: str,
        no_token: str,
        yes_price: float,
        no_price: float,
        size: float,
    ) -> Tuple[ExecutionResult, ExecutionResult]:
        """
        Execute an arbitrage trade by buying both YES and NO.

        Args:
            yes_token: YES token ID
            no_token: NO token ID
            yes_price: YES price
            no_price: NO price
            size: Number of contracts each side

        Returns:
            Tuple of (yes_result, no_result)
        """
        self._total_orders += 2

        if self.dry_run:
            logger.info(
                f"[DRY RUN] Arbitrage trade:\n"
                f"  BUY {size} YES @ ${yes_price:.3f} = ${size * yes_price:.2f}\n"
                f"  BUY {size} NO @ ${no_price:.3f} = ${size * no_price:.2f}\n"
                f"  Total cost: ${size * (yes_price + no_price):.2f}\n"
                f"  Expected payout: ${size:.2f}\n"
                f"  Gross profit: ${size * (1 - yes_price - no_price):.2f}"
            )

            # Create simulated results
            yes_order = ExecutionOrder(
                id=str(uuid.uuid4()),
                token_id=yes_token,
                side=OrderSide.BUY,
                price=yes_price,
                size=size,
                status=ExecutionStatus.FILLED,
                filled_size=size,
                average_fill_price=yes_price,
            )
            no_order = ExecutionOrder(
                id=str(uuid.uuid4()),
                token_id=no_token,
                side=OrderSide.BUY,
                price=no_price,
                size=size,
                status=ExecutionStatus.FILLED,
                filled_size=size,
                average_fill_price=no_price,
            )

            self._successful_orders += 2
            self._total_volume += size * (yes_price + no_price)

            return (
                ExecutionResult(success=True, order=yes_order),
                ExecutionResult(success=True, order=no_order),
            )

        # Live execution - submit both orders simultaneously
        logger.info(
            f"Executing arbitrage: {size} contracts @ "
            f"YES=${yes_price:.3f}, NO=${no_price:.3f}"
        )

        start_time = time.time()

        # Execute both orders in parallel
        yes_task = self.executor.place_buy_order(
            token_id=yes_token,
            price=yes_price,
            size=size,
            order_type=OrderType.IOC,  # Immediate-or-cancel for speed
        )
        no_task = self.executor.place_buy_order(
            token_id=no_token,
            price=no_price,
            size=size,
            order_type=OrderType.IOC,
        )

        yes_result, no_result = await asyncio.gather(yes_task, no_task)

        execution_time = (time.time() - start_time) * 1000
        logger.info(f"Arbitrage execution completed in {execution_time:.0f}ms")

        # Track results
        if yes_result.success:
            self._successful_orders += 1
            self._total_volume += size * yes_price
            self._orders[yes_result.order.id] = yes_result.order
        else:
            self._failed_orders += 1

        if no_result.success:
            self._successful_orders += 1
            self._total_volume += size * no_price
            self._orders[no_result.order.id] = no_result.order
        else:
            self._failed_orders += 1

        # Handle partial fills
        if yes_result.success and not no_result.success:
            logger.warning("YES filled but NO failed - attempting to cancel YES")
            if yes_result.order.exchange_order_id:
                await self.executor.cancel_order(yes_result.order.exchange_order_id)

        elif no_result.success and not yes_result.success:
            logger.warning("NO filled but YES failed - attempting to cancel NO")
            if no_result.order.exchange_order_id:
                await self.executor.cancel_order(no_result.order.exchange_order_id)

        # Update positions
        if yes_result.success and yes_result.order.filled_size > 0:
            self._update_position(yes_token, yes_result.order)
        if no_result.success and no_result.order.filled_size > 0:
            self._update_position(no_token, no_result.order)

        return yes_result, no_result

    def _update_position(self, token_id: str, order: ExecutionOrder):
        """Update position after order fill."""
        if token_id not in self._positions:
            self._positions[token_id] = Position(
                token_id=token_id,
                size=0,
                average_price=0,
            )

        pos = self._positions[token_id]

        if order.side == OrderSide.BUY:
            # Add to position
            new_size = pos.size + order.filled_size
            if new_size > 0:
                pos.average_price = (
                    (pos.size * pos.average_price + order.filled_size * order.average_fill_price)
                    / new_size
                )
            pos.size = new_size
        else:
            # Reduce position
            pos.size -= order.filled_size
            if pos.size <= 0:
                del self._positions[token_id]

    async def get_positions(self) -> List[Position]:
        """Get all current positions."""
        return list(self._positions.values())

    async def close_position(self, token_id: str) -> Optional[ExecutionResult]:
        """Close a position by selling."""
        if token_id not in self._positions:
            return None

        pos = self._positions[token_id]
        if pos.size <= 0:
            return None

        if self.dry_run:
            logger.info(f"[DRY RUN] Would close position: {token_id} size={pos.size}")
            return None

        # Get current price and sell
        book = await self.clob.get_order_book(token_id)
        if not book.best_bid:
            return None

        return await self.executor.place_sell_order(
            token_id=token_id,
            price=book.best_bid,
            size=pos.size,
        )

    async def close_all_positions(self):
        """Close all open positions."""
        for token_id in list(self._positions.keys()):
            await self.close_position(token_id)

    async def cancel_all_orders(self) -> int:
        """Cancel all open orders."""
        return await self.clob.cancel_all_orders()

    def get_order(self, order_id: str) -> Optional[ExecutionOrder]:
        """Get order by ID."""
        return self._orders.get(order_id)

    def get_recent_orders(self, limit: int = 10) -> List[ExecutionOrder]:
        """Get recent orders."""
        orders = sorted(
            self._orders.values(),
            key=lambda x: x.created_at,
            reverse=True,
        )
        return orders[:limit]
