"""
Stock screener: runs all signals against the watchlist and
returns ranked candidates with composite scores.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


from src.analysis.signals.base import BaseSignal, SignalResult
from src.analysis.signals.ema_crossover import EMACrossoverSignal
from src.analysis.signals.rsi import RSIDivergenceSignal
from src.analysis.signals.support_resistance import SupportResistanceSignal
from src.analysis.signals.volume import VolumeBreakoutSignal
from src.broker.base import BrokerBase
from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ScreenerResult:
    symbol: str
    direction: str
    composite_score: float
    entry: float
    target: float
    stop_loss: float
    risk_reward: float
    signals_fired: list[dict] = field(default_factory=list)
    timeframe: str = "daily"


def _build_signals() -> list[BaseSignal]:
    return [
        EMACrossoverSignal(),
        RSIDivergenceSignal(),
        SupportResistanceSignal(),
        VolumeBreakoutSignal(),
    ]


class Screener:
    def __init__(self, broker: BrokerBase):
        self.broker = broker
        self.signals = _build_signals()
        self.settings = get_settings()

    def run(self, symbols: Optional[list[str]] = None) -> list[ScreenerResult]:
        """
        Screen all symbols, run signals, compute weighted composite score,
        and return results sorted by score descending.
        """
        symbols = symbols or self.settings.watchlist
        weights = self.settings.signal_weights
        results: list[ScreenerResult] = []

        from_date = datetime.now() - timedelta(days=180)
        to_date = datetime.now()

        for symbol in symbols:
            try:
                df = self.broker.get_historical_data(
                    symbol,
                    interval="day",
                    from_date=from_date,
                    to_date=to_date,
                )
                if df.empty or len(df) < 60:
                    continue
            except Exception as e:
                logger.warning(f"Failed to fetch data for {symbol}: {e}")
                continue

            fired: list[SignalResult] = []
            for signal in self.signals:
                try:
                    result = signal.analyze(df, symbol)
                    if signal.is_valid(result):
                        fired.append(result)
                except Exception as e:
                    logger.warning(f"Signal {signal.name} failed for {symbol}: {e}")

            if not fired:
                continue

            # All fired signals must agree on direction (no conflicting signals)
            directions = {s.direction for s in fired}
            if len(directions) > 1:
                logger.debug(
                    f"{symbol}: conflicting signal directions {directions}, skipping"
                )
                continue

            direction = fired[0].direction

            # Weighted composite score
            total_weight = sum(weights.get(s.signal_name, 1.0) for s in fired)
            composite = sum(
                s.strength * weights.get(s.signal_name, 1.0) for s in fired
            ) / max(total_weight, 1)

            # Use the signal with highest strength for price levels
            best = max(fired, key=lambda s: s.strength)

            results.append(
                ScreenerResult(
                    symbol=symbol,
                    direction=direction,
                    composite_score=round(composite, 3),
                    entry=best.entry,
                    target=best.target,
                    stop_loss=best.stop_loss,
                    risk_reward=best.risk_reward,
                    signals_fired=[s.to_dict() for s in fired],
                    timeframe=best.timeframe,
                )
            )

        results.sort(key=lambda r: r.composite_score, reverse=True)
        logger.info(f"Screener found {len(results)} setups from {len(symbols)} symbols")
        return results
