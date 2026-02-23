"""
Base signal contract.  Every signal returns a SignalResult or None.
Signals never talk to a broker or DB — they only consume a DataFrame.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class SignalResult:
    signal_name: str
    direction: str  # "BUY" | "SELL"
    strength: float  # 0.0–1.0  (higher = more conviction)
    entry: float
    target: float
    stop_loss: float
    timeframe: str  # "daily" | "weekly"
    details: dict = field(default_factory=dict)  # Signal-specific metadata

    @property
    def risk_reward(self) -> float:
        if self.direction == "BUY":
            reward = self.target - self.entry
            risk = self.entry - self.stop_loss
        else:
            reward = self.entry - self.target
            risk = self.stop_loss - self.entry
        return round(reward / risk, 2) if risk > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "signal_name": self.signal_name,
            "direction": self.direction,
            "strength": round(self.strength, 3),
            "entry": self.entry,
            "target": self.target,
            "stop_loss": self.stop_loss,
            "timeframe": self.timeframe,
            "risk_reward": self.risk_reward,
            **self.details,
        }


class BaseSignal(ABC):
    """
    All signals inherit from here.
    Subclasses implement `analyze()` and set `name`.
    """

    name: str
    min_risk_reward: float = 2.0

    @abstractmethod
    def analyze(self, df: pd.DataFrame, symbol: str) -> Optional[SignalResult]:
        """
        Analyze OHLCV data and return a SignalResult if conditions are met.
        Return None if no valid setup exists.
        `df` is expected to have columns: open, high, low, close, volume
        indexed by datetime, sorted ascending.
        """

    def is_valid(self, result: Optional[SignalResult]) -> bool:
        if result is None:
            return False
        return result.strength > 0 and result.risk_reward >= self.min_risk_reward
