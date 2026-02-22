"""
EMA Crossover signal.

Swing trade setup: 20 EMA crosses above/below 50 EMA on daily chart
with price confirming above/below both EMAs.

Entry  : Close above/below both EMAs on crossover candle
Target : 1× ATR(14) projected
Stop   : Below/above the swing low/high before crossover
"""
from typing import Optional

import pandas as pd
import pandas_ta as ta

from src.analysis.signals.base import BaseSignal, SignalResult


class EMACrossoverSignal(BaseSignal):
    name = "ema_crossover"

    def __init__(self, fast: int = 20, slow: int = 50, atr_period: int = 14,
                 atr_target_multiplier: float = 2.0):
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period
        self.atr_target_mult = atr_target_multiplier

    def analyze(self, df: pd.DataFrame, symbol: str) -> Optional[SignalResult]:
        if len(df) < self.slow + 5:
            return None

        df = df.copy()
        df["ema_fast"] = ta.ema(df["close"], length=self.fast)
        df["ema_slow"] = ta.ema(df["close"], length=self.slow)
        df["atr"]      = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)

        last  = df.iloc[-1]
        prev  = df.iloc[-2]

        # Detect crossover in the last candle
        bullish_cross = (prev["ema_fast"] <= prev["ema_slow"] and
                         last["ema_fast"] > last["ema_slow"] and
                         last["close"] > last["ema_fast"])

        bearish_cross = (prev["ema_fast"] >= prev["ema_slow"] and
                         last["ema_fast"] < last["ema_slow"] and
                         last["close"] < last["ema_fast"])

        if not bullish_cross and not bearish_cross:
            return None

        atr   = last["atr"]
        close = last["close"]

        if bullish_cross:
            # Swing low = lowest low of last 5 candles before signal
            swing_low  = df["low"].iloc[-6:-1].min()
            stop_loss  = round(swing_low - 0.5 * atr, 2)
            target     = round(close + self.atr_target_mult * atr, 2)
            direction  = "BUY"
        else:
            swing_high = df["high"].iloc[-6:-1].max()
            stop_loss  = round(swing_high + 0.5 * atr, 2)
            target     = round(close - self.atr_target_mult * atr, 2)
            direction  = "SELL"

        # Strength: how clean is the crossover gap?
        gap_pct  = abs(last["ema_fast"] - last["ema_slow"]) / last["ema_slow"]
        strength = min(1.0, gap_pct * 50)  # Normalised; crossovers >2% gap → strength 1.0

        return SignalResult(
            signal_name=self.name,
            direction=direction,
            strength=round(strength, 3),
            entry=round(close, 2),
            target=target,
            stop_loss=stop_loss,
            timeframe="daily",
            details={
                "ema_fast": round(last["ema_fast"], 2),
                "ema_slow": round(last["ema_slow"], 2),
                "atr":      round(atr, 2),
                "gap_pct":  round(gap_pct * 100, 2),
            },
        )
