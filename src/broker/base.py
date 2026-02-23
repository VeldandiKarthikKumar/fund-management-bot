"""
Abstract broker interface.
All broker-specific code lives in concrete adapters (e.g. zerodha.py).
Pipelines and signals only depend on this interface — swap brokers without
touching analysis logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass
class Instrument:
    symbol: str
    token: int
    exchange: str
    lot_size: int = 1
    tick_size: float = 0.05


@dataclass
class Quote:
    symbol: str
    last_price: float
    open: float
    high: float
    low: float
    close: float  # Previous day close
    volume: int
    timestamp: datetime


class BrokerBase(ABC):
    """
    Minimal interface every broker adapter must implement.
    Methods are synchronous; wrap in asyncio.to_thread if needed.
    """

    @abstractmethod
    def authenticate(self, request_token: str) -> str:
        """Exchange a one-time request token for a session access token."""

    @abstractmethod
    def set_access_token(self, token: str) -> None:
        """Load a previously obtained access token into the client."""

    @abstractmethod
    def get_historical_data(
        self,
        symbol: str,
        interval: str,  # "day" | "week" | "60minute" | "15minute" | "5minute"
        from_date: datetime,
        to_date: datetime,
        exchange: str = "NSE",
    ) -> pd.DataFrame:
        """
        Return OHLCV DataFrame with columns:
          date, open, high, low, close, volume
        Indexed by date ascending.
        """

    @abstractmethod
    def get_quote(self, symbols: list[str], exchange: str = "NSE") -> dict[str, Quote]:
        """Return live quotes keyed by symbol."""

    @abstractmethod
    def get_instrument(self, symbol: str, exchange: str = "NSE") -> Instrument:
        """Resolve a symbol to its broker instrument details."""

    @abstractmethod
    def is_market_open(self) -> bool:
        """Return True if NSE is currently in a live session."""

    @abstractmethod
    def get_holdings(self) -> list[dict]:
        """Return current demat holdings (for portfolio reconciliation)."""

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return intraday / short-term positions."""

    def compute_quantity(
        self, capital: float, entry: float, stop: float, risk_pct: float
    ) -> int:
        """
        Position sizing via fixed-risk model.
        risk_pct: fraction of capital to risk (e.g. 0.015 for 1.5%)
        """
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0:
            return 0
        risk_amount = capital * risk_pct
        return max(1, int(risk_amount / risk_per_share))
