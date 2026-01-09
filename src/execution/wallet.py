"""
Wallet management for Polymarket trading.

Handles:
- Polygon wallet creation and management
- Transaction signing
- Gas estimation
- USDC balance tracking
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from loguru import logger
from web3 import Web3

# Handle web3 v6+ middleware import change
try:
    from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware
except ImportError:
    try:
        from web3.middleware import geth_poa_middleware
    except ImportError:
        geth_poa_middleware = None

# Contract addresses on Polygon
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC on Polygon
POLYMARKET_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # CTF Exchange


@dataclass
class WalletBalance:
    """Wallet balance information."""

    matic: Decimal
    usdc: Decimal
    timestamp: datetime

    @property
    def has_gas(self) -> bool:
        """Check if wallet has enough MATIC for gas."""
        return self.matic >= Decimal("0.01")

    @property
    def has_trading_funds(self) -> bool:
        """Check if wallet has USDC for trading."""
        return self.usdc > 0


@dataclass
class TransactionResult:
    """Result of a blockchain transaction."""

    success: bool
    tx_hash: Optional[str] = None
    gas_used: int = 0
    gas_price_gwei: float = 0.0
    error: Optional[str] = None

    @property
    def gas_cost_matic(self) -> float:
        """Gas cost in MATIC."""
        return (self.gas_used * self.gas_price_gwei) / 1e9


class PolygonWallet:
    """
    Polygon wallet for signing transactions.

    Example:
        wallet = PolygonWallet(private_key="...")
        balance = await wallet.get_balance()
        print(f"USDC: {balance.usdc}")
    """

    def __init__(
        self,
        private_key: str,
        rpc_url: str = "https://polygon-rpc.com",
        chain_id: int = 137,
    ):
        """
        Initialize wallet.

        Args:
            private_key: Wallet private key (without 0x prefix)
            rpc_url: Polygon RPC endpoint
            chain_id: Chain ID (137 for mainnet)
        """
        self.rpc_url = rpc_url
        self.chain_id = chain_id

        # Create Web3 instance
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if geth_poa_middleware:
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        # Create account from private key
        if not private_key.startswith("0x"):
            private_key = f"0x{private_key}"

        self._account: LocalAccount = Account.from_key(private_key)
        self.address = self._account.address

        # USDC contract ABI (minimal for balanceOf and approve)
        self._usdc_abi = [
            {
                "constant": True,
                "inputs": [{"name": "account", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}],
                "type": "function",
            },
            {
                "constant": False,
                "inputs": [
                    {"name": "spender", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                ],
                "name": "approve",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function",
            },
            {
                "constant": True,
                "inputs": [
                    {"name": "owner", "type": "address"},
                    {"name": "spender", "type": "address"},
                ],
                "name": "allowance",
                "outputs": [{"name": "", "type": "uint256"}],
                "type": "function",
            },
        ]

        self._usdc_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS),
            abi=self._usdc_abi,
        )

        logger.info(f"Wallet initialized: {self.address}")

    @property
    def account(self) -> LocalAccount:
        """Get the account object."""
        return self._account

    async def get_balance(self) -> WalletBalance:
        """
        Get wallet balances.

        Returns:
            WalletBalance with MATIC and USDC amounts
        """
        try:
            # Get MATIC balance
            matic_wei = self.w3.eth.get_balance(self.address)
            matic = Decimal(str(self.w3.from_wei(matic_wei, "ether")))

            # Get USDC balance (6 decimals)
            usdc_raw = self._usdc_contract.functions.balanceOf(self.address).call()
            usdc = Decimal(str(usdc_raw)) / Decimal("1000000")

            return WalletBalance(
                matic=matic,
                usdc=usdc,
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return WalletBalance(
                matic=Decimal("0"),
                usdc=Decimal("0"),
                timestamp=datetime.now(),
            )

    async def get_usdc_allowance(
        self,
        spender: str = POLYMARKET_EXCHANGE,
    ) -> Decimal:
        """
        Get USDC allowance for a spender.

        Args:
            spender: Spender address (default: Polymarket exchange)

        Returns:
            Allowance amount in USDC
        """
        try:
            allowance_raw = self._usdc_contract.functions.allowance(
                self.address,
                Web3.to_checksum_address(spender),
            ).call()
            return Decimal(str(allowance_raw)) / Decimal("1000000")
        except Exception as e:
            logger.error(f"Failed to get allowance: {e}")
            return Decimal("0")

    async def approve_usdc(
        self,
        spender: str = POLYMARKET_EXCHANGE,
        amount: Optional[Decimal] = None,
    ) -> TransactionResult:
        """
        Approve USDC spending.

        Args:
            spender: Spender address
            amount: Amount to approve (None = unlimited)

        Returns:
            TransactionResult
        """
        try:
            # Use max uint256 for unlimited approval
            if amount is None:
                amount_raw = 2**256 - 1
            else:
                amount_raw = int(amount * Decimal("1000000"))

            # Build transaction
            nonce = self.w3.eth.get_transaction_count(self.address)
            gas_price = self.w3.eth.gas_price

            tx = self._usdc_contract.functions.approve(
                Web3.to_checksum_address(spender),
                amount_raw,
            ).build_transaction(
                {
                    "from": self.address,
                    "nonce": nonce,
                    "gas": 100000,
                    "gasPrice": gas_price,
                    "chainId": self.chain_id,
                }
            )

            # Sign and send
            signed_tx = self._account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)

            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            return TransactionResult(
                success=receipt["status"] == 1,
                tx_hash=tx_hash.hex(),
                gas_used=receipt["gasUsed"],
                gas_price_gwei=gas_price / 1e9,
            )

        except Exception as e:
            logger.error(f"Approval failed: {e}")
            return TransactionResult(
                success=False,
                error=str(e),
            )

    def sign_message(self, message: str) -> str:
        """
        Sign a message with the wallet.

        Args:
            message: Message to sign

        Returns:
            Signature as hex string
        """
        from eth_account.messages import encode_defunct

        message_hash = encode_defunct(text=message)
        signed = self._account.sign_message(message_hash)
        return signed.signature.hex()

    async def estimate_gas(self, tx_data: Dict[str, Any]) -> int:
        """
        Estimate gas for a transaction.

        Args:
            tx_data: Transaction data

        Returns:
            Estimated gas units
        """
        try:
            return self.w3.eth.estimate_gas(tx_data)
        except Exception as e:
            logger.warning(f"Gas estimation failed: {e}")
            return 100000  # Default fallback

    async def get_gas_price(self) -> int:
        """Get current gas price in wei."""
        return self.w3.eth.gas_price


class WalletManager:
    """
    Manages wallet operations for trading.

    Handles:
    - Balance tracking
    - Allowance management
    - Multi-wallet support (future)
    """

    def __init__(
        self,
        private_key: str = "",
        rpc_url: str = "https://polygon-rpc.com",
        min_matic_threshold: float = 0.1,
        min_usdc_threshold: float = 10.0,
    ):
        """
        Initialize wallet manager.

        Args:
            private_key: Wallet private key
            rpc_url: Polygon RPC URL
            min_matic_threshold: Minimum MATIC for gas
            min_usdc_threshold: Minimum USDC for trading
        """
        self.min_matic = Decimal(str(min_matic_threshold))
        self.min_usdc = Decimal(str(min_usdc_threshold))

        self._wallet: Optional[PolygonWallet] = None
        self._balance: Optional[WalletBalance] = None

        if private_key:
            self._wallet = PolygonWallet(
                private_key=private_key,
                rpc_url=rpc_url,
            )

    @property
    def wallet(self) -> Optional[PolygonWallet]:
        """Get the wallet instance."""
        return self._wallet

    @property
    def address(self) -> Optional[str]:
        """Get wallet address."""
        return self._wallet.address if self._wallet else None

    @property
    def is_configured(self) -> bool:
        """Check if wallet is configured."""
        return self._wallet is not None

    async def initialize(self) -> bool:
        """
        Initialize wallet and check balances.

        Returns:
            True if wallet is ready for trading
        """
        if not self._wallet:
            logger.error("No wallet configured")
            return False

        # Get balance
        self._balance = await self._wallet.get_balance()

        logger.info(
            f"Wallet balance: "
            f"MATIC={self._balance.matic:.4f}, "
            f"USDC={self._balance.usdc:.2f}"
        )

        # Check minimums
        if self._balance.matic < self.min_matic:
            logger.warning(
                f"Low MATIC balance ({self._balance.matic:.4f}). "
                f"Need at least {self.min_matic} for gas."
            )

        if self._balance.usdc < self.min_usdc:
            logger.warning(
                f"Low USDC balance ({self._balance.usdc:.2f}). "
                f"Need at least {self.min_usdc} for trading."
            )

        # Check allowance
        allowance = await self._wallet.get_usdc_allowance()
        if allowance < self._balance.usdc:
            logger.info("Setting USDC approval for Polymarket...")
            result = await self._wallet.approve_usdc()
            if not result.success:
                logger.error(f"Approval failed: {result.error}")
                return False

        return self._balance.has_gas and self._balance.has_trading_funds

    async def get_available_capital(self) -> float:
        """Get available trading capital."""
        if not self._wallet:
            return 0.0

        balance = await self._wallet.get_balance()
        return float(balance.usdc)

    async def refresh_balance(self) -> WalletBalance:
        """Refresh and return current balance."""
        if not self._wallet:
            return WalletBalance(
                matic=Decimal("0"),
                usdc=Decimal("0"),
                timestamp=datetime.now(),
            )

        self._balance = await self._wallet.get_balance()
        return self._balance

    def sign_order(self, order_data: str) -> str:
        """Sign order data."""
        if not self._wallet:
            raise ValueError("No wallet configured")
        return self._wallet.sign_message(order_data)


async def main():
    """Test wallet functionality."""
    import os

    private_key = os.getenv("PRIVATE_KEY", "")

    if not private_key:
        logger.info("No private key configured. Set PRIVATE_KEY env var.")
        return

    manager = WalletManager(private_key=private_key)
    ready = await manager.initialize()

    if ready:
        logger.info("Wallet ready for trading!")
        capital = await manager.get_available_capital()
        logger.info(f"Available capital: ${capital:.2f}")
    else:
        logger.warning("Wallet not ready")


if __name__ == "__main__":
    asyncio.run(main())
