"""
Volume Breakout signal.

Identifies stocks with exceptional volume (≥2× 20-day average) combined
with a strong directional candle (body ≥ 60% of range) — indicating
institutional accumulation or distribution.

Entry  : Today's close
Target : ATR-based projection
Stop   : Low of the signal candle (BUY) / High of signal candle (SELL)
"""

from typing import Optional

import pandas as pd
import pandas_ta as ta

from src.analysis.signals.base import BaseSignal, SignalResult

_MIN_VOLUME_MULTIPLIER = 2.0
_MIN_BODY_RATIO = 0.60  # Body must be 60%+ of (high - low)


class VolumeBreakoutSignal(BaseSignal):
    name = "volume_breakout"

    def __init__(
        self,
        vol_ma_period: int = 20,
        atr_period: int = 14,
        atr_target_multiplier: float = 2.0,
    ):
        self.vol_ma = vol_ma_period
        self.atr_per = atr_period
        self.atr_mult = atr_target_multiplier

    def analyze(self, df: pd.DataFrame, symbol: str) -> Optional[SignalResult]:
        if len(df) < self.vol_ma + 5:
            return None

        df = df.copy()
        df["vol_ma"] = df["volume"].rolling(self.vol_ma).mean()
        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=self.atr_per)
        df = df.dropna()

        last = df.iloc[-1]
        vol_ratio = last["volume"] / last["vol_ma"] if last["vol_ma"] > 0 else 0

        if vol_ratio < _MIN_VOLUME_MULTIPLIER:
            return None

        candle_range = last["high"] - last["low"]
        if candle_range <= 0:
            return None

        body = abs(last["close"] - last["open"])
        body_pct = body / candle_range

        if body_pct < _MIN_BODY_RATIO:
            return None  # Indecisive candle (doji / hammer-like) — skip

        is_bullish = last["close"] > last["open"]
        close = last["close"]
        atr = last["atr"]

        if is_bullish:
            stop_loss = round(last["low"] - 0.2 * atr, 2)
            target = round(close + self.atr_mult * atr, 2)
            direction = "BUY"
        else:
            stop_loss = round(last["high"] + 0.2 * atr, 2)
            target = round(close - self.atr_mult * atr, 2)
            direction = "SELL"

        # Strength: vol ratio normalised, capped at 1.0
        strength = min(1.0, (vol_ratio - _MIN_VOLUME_MULTIPLIER) / 3 + 0.5)

        return SignalResult(
            signal_name=self.name,
            direction=direction,
            strength=round(strength, 3),
            entry=round(close, 2),
            target=target,
            stop_loss=stop_loss,
            timeframe="daily",
            details={
                "vol_ratio": round(vol_ratio, 2),
                "body_pct": round(body_pct * 100, 1),
                "atr": round(atr, 2),
            },
        )
