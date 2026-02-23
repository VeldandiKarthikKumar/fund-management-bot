"""
Unit tests for all signal implementations.
Uses synthetic OHLCV data — no broker calls.
"""

import numpy as np
import pandas as pd

from src.analysis.signals.ema_crossover import EMACrossoverSignal
from src.analysis.signals.rsi import RSIDivergenceSignal
from src.analysis.signals.support_resistance import SupportResistanceSignal
from src.analysis.signals.volume import VolumeBreakoutSignal


def _make_df(n: int = 120, trend: str = "up", base: float = 1000.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    if trend == "up":
        closes = base + np.cumsum(np.random.normal(2, 5, n))
    elif trend == "down":
        closes = base + np.cumsum(np.random.normal(-2, 5, n))
    else:
        closes = base + np.random.normal(0, 5, n)

    closes = np.maximum(closes, 10)
    df = pd.DataFrame(
        {
            "open": closes * np.random.uniform(0.99, 1.01, n),
            "high": closes * np.random.uniform(1.00, 1.03, n),
            "low": closes * np.random.uniform(0.97, 1.00, n),
            "close": closes,
            "volume": np.random.randint(100_000, 5_000_000, n),
        },
        index=dates,
    )
    return df


class TestEMACrossover:
    def test_returns_result_or_none(self):
        signal = EMACrossoverSignal()
        df = _make_df(100)
        result = signal.analyze(df, "TEST")
        # May or may not signal — just assert no crash and correct types
        assert result is None or result.direction in ("BUY", "SELL")

    def test_insufficient_data_returns_none(self):
        signal = EMACrossoverSignal()
        df = _make_df(10)
        assert signal.analyze(df, "TEST") is None

    def test_risk_reward_positive(self):
        signal = EMACrossoverSignal()
        df = _make_df(100)
        result = signal.analyze(df, "TEST")
        if result:
            assert result.risk_reward > 0

    def test_signal_name(self):
        assert EMACrossoverSignal.name == "ema_crossover"


class TestRSIDivergence:
    def test_returns_result_or_none(self):
        signal = RSIDivergenceSignal()
        df = _make_df(100, trend="down")
        result = signal.analyze(df, "TEST")
        assert result is None or result.direction in ("BUY", "SELL")

    def test_insufficient_data_returns_none(self):
        signal = RSIDivergenceSignal()
        df = _make_df(20)
        assert signal.analyze(df, "TEST") is None

    def test_signal_name(self):
        assert RSIDivergenceSignal.name == "rsi_divergence"


class TestSupportResistance:
    def test_returns_result_or_none(self):
        signal = SupportResistanceSignal()
        df = _make_df(120)
        result = signal.analyze(df, "TEST")
        assert result is None or result.direction in ("BUY", "SELL")

    def test_insufficient_data_returns_none(self):
        signal = SupportResistanceSignal()
        df = _make_df(30)
        assert signal.analyze(df, "TEST") is None


class TestVolumeBreakout:
    def test_detects_high_volume_bullish(self):
        signal = VolumeBreakoutSignal()
        df = _make_df(60, trend="up")
        # Force a high-volume bullish candle on the last row
        df.iloc[-1, df.columns.get_loc("volume")] = int(df["volume"].mean() * 5)
        df.iloc[-1, df.columns.get_loc("close")] = df["close"].iloc[-1] * 1.02
        df.iloc[-1, df.columns.get_loc("open")] = df["close"].iloc[-1] * 0.99
        result = signal.analyze(df, "TEST")
        if result:
            assert result.direction == "BUY"
            assert result.strength > 0

    def test_low_volume_returns_none(self):
        signal = VolumeBreakoutSignal()
        df = _make_df(60)
        # Force volume below threshold
        df["volume"] = 100_000
        result = signal.analyze(df, "TEST")
        assert result is None

    def test_signal_result_fields(self):
        signal = VolumeBreakoutSignal()
        df = _make_df(60, trend="up")
        df.iloc[-1, df.columns.get_loc("volume")] = int(df["volume"].mean() * 5)
        df.iloc[-1, df.columns.get_loc("close")] = df["close"].iloc[-1] * 1.02
        df.iloc[-1, df.columns.get_loc("open")] = df["close"].iloc[-1] * 0.99
        result = signal.analyze(df, "TEST")
        if result:
            assert result.entry > 0
            assert result.target > 0
            assert result.stop_loss > 0
            assert 0 <= result.strength <= 1.0
