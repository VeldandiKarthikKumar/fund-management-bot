"""
RSI Divergence signal.

Bullish divergence : Price makes lower low, RSI makes higher low → reversal up
Bearish divergence : Price makes higher high, RSI makes lower high → reversal down

Uses 14-period RSI on daily chart.  Checks last 20 candles for swing pivots.
"""

from typing import Optional

import pandas as pd
import pandas_ta as ta

from src.analysis.signals.base import BaseSignal, SignalResult


def _find_swing_lows(series: pd.Series, window: int = 3) -> pd.Series:
    """Return boolean mask of swing low candles."""
    return series == series.rolling(2 * window + 1, center=True).min()


def _find_swing_highs(series: pd.Series, window: int = 3) -> pd.Series:
    return series == series.rolling(2 * window + 1, center=True).max()


class RSIDivergenceSignal(BaseSignal):
    name = "rsi_divergence"

    def __init__(
        self,
        rsi_period: int = 14,
        lookback: int = 25,
        oversold: float = 40,
        overbought: float = 60,
        atr_target_multiplier: float = 2.5,
    ):
        self.rsi_period = rsi_period
        self.lookback = lookback
        self.oversold = oversold  # RSI below this = potential bullish div zone
        self.overbought = overbought  # RSI above this = potential bearish div zone
        self.atr_mult = atr_target_multiplier

    def analyze(self, df: pd.DataFrame, symbol: str) -> Optional[SignalResult]:
        if len(df) < self.lookback + self.rsi_period:
            return None

        df = df.copy()
        df["rsi"] = ta.rsi(df["close"], length=self.rsi_period)
        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
        df = df.dropna()

        recent = df.iloc[-self.lookback :]

        # ── Bullish divergence ─────────────────────────────────────────────
        price_lows = _find_swing_lows(recent["low"])
        rsi_vals = recent["rsi"]
        low_idx = recent[price_lows].index.tolist()

        if len(low_idx) >= 2:
            p1, p2 = low_idx[-2], low_idx[-1]
            price_made_lower_low = recent.loc[p2, "low"] < recent.loc[p1, "low"]
            rsi_made_higher_low = rsi_vals.loc[p2] > rsi_vals.loc[p1]
            rsi_in_zone = rsi_vals.loc[p2] < self.oversold + 15

            if price_made_lower_low and rsi_made_higher_low and rsi_in_zone:
                last = df.iloc[-1]
                atr = last["atr"]
                close = last["close"]
                stop_loss = round(recent.loc[p2, "low"] - 0.3 * atr, 2)
                target = round(close + self.atr_mult * atr, 2)
                # Strength proportional to RSI divergence magnitude
                rsi_div = rsi_vals.loc[p2] - rsi_vals.loc[p1]
                strength = min(1.0, rsi_div / 15)
                return SignalResult(
                    signal_name=self.name,
                    direction="BUY",
                    strength=round(strength, 3),
                    entry=round(close, 2),
                    target=target,
                    stop_loss=stop_loss,
                    timeframe="daily",
                    details={
                        "rsi_current": round(rsi_vals.iloc[-1], 1),
                        "rsi_p1": round(rsi_vals.loc[p1], 1),
                        "rsi_p2": round(rsi_vals.loc[p2], 1),
                        "type": "bullish_divergence",
                    },
                )

        # ── Bearish divergence ─────────────────────────────────────────────
        price_highs = _find_swing_highs(recent["high"])
        high_idx = recent[price_highs].index.tolist()

        if len(high_idx) >= 2:
            p1, p2 = high_idx[-2], high_idx[-1]
            price_made_higher_high = recent.loc[p2, "high"] > recent.loc[p1, "high"]
            rsi_made_lower_high = rsi_vals.loc[p2] < rsi_vals.loc[p1]
            rsi_in_zone = rsi_vals.loc[p2] > self.overbought - 15

            if price_made_higher_high and rsi_made_lower_high and rsi_in_zone:
                last = df.iloc[-1]
                atr = last["atr"]
                close = last["close"]
                stop_loss = round(recent.loc[p2, "high"] + 0.3 * atr, 2)
                target = round(close - self.atr_mult * atr, 2)
                rsi_div = rsi_vals.loc[p1] - rsi_vals.loc[p2]
                strength = min(1.0, rsi_div / 15)
                return SignalResult(
                    signal_name=self.name,
                    direction="SELL",
                    strength=round(strength, 3),
                    entry=round(close, 2),
                    target=target,
                    stop_loss=stop_loss,
                    timeframe="daily",
                    details={
                        "rsi_current": round(rsi_vals.iloc[-1], 1),
                        "rsi_p1": round(rsi_vals.loc[p1], 1),
                        "rsi_p2": round(rsi_vals.loc[p2], 1),
                        "type": "bearish_divergence",
                    },
                )

        return None
