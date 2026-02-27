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

    def run(
        self,
        symbols: Optional[list[str]] = None,
        to_date: Optional[datetime] = None,
    ) -> list[ScreenerResult]:
        """
        Screen all symbols, run signals, compute weighted composite score,
        and return results sorted by score descending.

        `to_date` defaults to now. Pass `datetime.combine(date.today(),
        datetime.min.time())` when calling during market hours to exclude
        today's incomplete candle from signal calculations.
        """
        symbols = self.settings.watchlist if symbols is None else symbols
        if not symbols:
            return []
        weights = self.settings.signal_weights
        results: list[ScreenerResult] = []

        to_date = to_date or datetime.now()
        from_date = to_date - timedelta(days=180)

        # Pre-resolve symbol tokens and log the mapping before fetching data.
        # Catches bad/missing tokens early and avoids per-symbol master scans.
        if hasattr(self.broker, "warm_instrument_cache"):
            self.broker.warm_instrument_cache(symbols)

        n_fetch_error = 0
        n_insufficient = 0
        n_no_signal = 0
        n_rr_fail: dict[str, int] = {}
        n_consensus = 0

        for symbol in symbols:
            try:
                df = self.broker.get_historical_data(
                    symbol,
                    interval="day",
                    from_date=from_date,
                    to_date=to_date,
                )
                if df.empty or len(df) < 60:
                    logger.debug(f"{symbol}: insufficient data ({len(df)} bars), skipping")
                    n_insufficient += 1
                    continue
            except Exception as e:
                logger.warning(f"Failed to fetch data for {symbol}: {e}")
                n_fetch_error += 1
                continue

            fired: list[SignalResult] = []
            for signal in self.signals:
                try:
                    result = signal.analyze(df, symbol)
                    if result is None:
                        continue
                    if signal.is_valid(result):
                        fired.append(result)
                    else:
                        logger.debug(
                            f"{symbol} [{signal.name}]: fired but failed validation "
                            f"(R:R={result.risk_reward:.2f}, strength={result.strength:.2f})"
                        )
                        n_rr_fail[signal.name] = n_rr_fail.get(signal.name, 0) + 1
                except Exception as e:
                    logger.warning(f"Signal {signal.name} failed for {symbol}: {e}")

            if not fired:
                n_no_signal += 1
                continue

            # All fired signals must agree on direction (no conflicting signals)
            directions = {s.direction for s in fired}
            if len(directions) > 1:
                logger.info(
                    f"{symbol}: conflicting signal directions {directions}, skipping"
                )
                n_consensus += 1
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
        rr_summary = ", ".join(f"{k}={v}" for k, v in n_rr_fail.items()) or "0"
        logger.info(
            f"Screener found {len(results)} setups from {len(symbols)} symbols — "
            f"fetch_errors={n_fetch_error}, insufficient_bars={n_insufficient}, "
            f"no_signal={n_no_signal}, rr_fail={rr_summary}, consensus_conflict={n_consensus}"
        )
        return results
