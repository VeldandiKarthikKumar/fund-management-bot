"""
Support / Resistance Breakout signal.

Identifies key S/R levels from recent pivot highs/lows (60-day lookback).
Signals a trade when price closes decisively above resistance or below support
with above-average volume confirmation.

Entry  : Next day open (or current close + small buffer)
Target : Next major S/R level beyond breakout
Stop   : Just below broken resistance (or above broken support)
"""
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from src.analysis.signals.base import BaseSignal, SignalResult

# Two levels within this % are merged into one
_LEVEL_MERGE_THRESHOLD = 0.005   # 0.5%
# Breakout candle must close this % beyond level
_BREAKOUT_THRESHOLD = 0.003      # 0.3%
# Volume must be this many times the 20-day average
_MIN_VOLUME_RATIO = 1.3


def _cluster_levels(levels: list[float], threshold: float) -> list[float]:
    """Merge nearby price levels into clusters."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters = [levels[0]]
    for level in levels[1:]:
        if (level - clusters[-1]) / clusters[-1] > threshold:
            clusters.append(level)
        else:
            clusters[-1] = (clusters[-1] + level) / 2  # Average
    return clusters


class SupportResistanceSignal(BaseSignal):
    name = "support_resistance"

    def __init__(self, lookback: int = 60, pivot_window: int = 5,
                 atr_target_multiplier: float = 2.0):
        self.lookback   = lookback
        self.pivot_win  = pivot_window
        self.atr_mult   = atr_target_multiplier

    def analyze(self, df: pd.DataFrame, symbol: str) -> Optional[SignalResult]:
        if len(df) < self.lookback + 20:
            return None

        df = df.copy()
        df["atr"]       = ta.atr(df["high"], df["low"], df["close"], length=14)
        df["vol_ma20"]  = df["volume"].rolling(20).mean()
        df = df.dropna()

        recent   = df.iloc[-self.lookback:]
        last     = df.iloc[-1]
        close    = last["close"]
        atr      = last["atr"]
        vol_ratio = last["volume"] / last["vol_ma20"] if last["vol_ma20"] > 0 else 0

        # ── Gather pivot highs and lows ─────────────────────────────────────
        w = self.pivot_win
        swing_highs = [
            recent["high"].iloc[i]
            for i in range(w, len(recent) - w)
            if recent["high"].iloc[i] == recent["high"].iloc[i-w:i+w+1].max()
        ]
        swing_lows = [
            recent["low"].iloc[i]
            for i in range(w, len(recent) - w)
            if recent["low"].iloc[i] == recent["low"].iloc[i-w:i+w+1].min()
        ]

        resistance_levels = _cluster_levels(swing_highs, _LEVEL_MERGE_THRESHOLD)
        support_levels    = _cluster_levels(swing_lows,  _LEVEL_MERGE_THRESHOLD)

        # ── Breakout above resistance ───────────────────────────────────────
        for resistance in reversed(resistance_levels):
            if close > resistance * (1 + _BREAKOUT_THRESHOLD) and vol_ratio >= _MIN_VOLUME_RATIO:
                # Target = next resistance level above, or ATR-based
                next_targets = [r for r in resistance_levels if r > close]
                target = min(next_targets) if next_targets else round(close + self.atr_mult * atr, 2)
                stop   = round(resistance - 0.5 * atr, 2)
                strength = min(1.0, (vol_ratio - _MIN_VOLUME_RATIO) / 2 + 0.5)
                return SignalResult(
                    signal_name=self.name,
                    direction="BUY",
                    strength=round(strength, 3),
                    entry=round(close, 2),
                    target=round(target, 2),
                    stop_loss=stop,
                    timeframe="daily",
                    details={
                        "broken_level": round(resistance, 2),
                        "vol_ratio":    round(vol_ratio, 2),
                        "type": "resistance_breakout",
                    },
                )

        # ── Breakdown below support ────────────────────────────────────────
        for support in support_levels:
            if close < support * (1 - _BREAKOUT_THRESHOLD) and vol_ratio >= _MIN_VOLUME_RATIO:
                next_targets = [s for s in support_levels if s < close]
                target = max(next_targets) if next_targets else round(close - self.atr_mult * atr, 2)
                stop   = round(support + 0.5 * atr, 2)
                strength = min(1.0, (vol_ratio - _MIN_VOLUME_RATIO) / 2 + 0.5)
                return SignalResult(
                    signal_name=self.name,
                    direction="SELL",
                    strength=round(strength, 3),
                    entry=round(close, 2),
                    target=round(target, 2),
                    stop_loss=stop,
                    timeframe="daily",
                    details={
                        "broken_level": round(support, 2),
                        "vol_ratio":    round(vol_ratio, 2),
                        "type": "support_breakdown",
                    },
                )

        return None
