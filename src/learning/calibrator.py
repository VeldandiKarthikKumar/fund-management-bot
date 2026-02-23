"""
Signal calibrator — runs weekly (Saturday morning) to adjust signal weights
based on accumulated performance statistics.

Rules:
  - Signals with win_rate > 60% and avg_pnl > 1.5%  → weight += 0.1 (max 2.0)
  - Signals with win_rate < 35% or avg_pnl < -1%    → weight -= 0.1 (min 0.1)
  - Signals with < 10 executed trades                → weight unchanged (not enough data)

Weights are written to SignalPerformance table and loaded into config at startup.
"""

import logging
from datetime import datetime

from src.db.connection import get_session
from src.db.models import SignalPerformance

logger = logging.getLogger(__name__)

_MIN_TRADES_FOR_CALIBRATION = 10
_HIGH_WIN_RATE = 0.60
_LOW_WIN_RATE = 0.35
_HIGH_AVG_PNL = 1.5  # %
_LOW_AVG_PNL = -1.0  # %
_WEIGHT_STEP = 0.1
_MAX_WEIGHT = 2.0
_MIN_WEIGHT = 0.1


def run() -> dict[str, float]:
    """
    Calibrate all signal weights. Returns the new weight map.
    Called by the weekly scheduler (or manually via /fundbot calibrate).
    """
    logger.info("Running signal calibrator")
    updated_weights: dict[str, float] = {}

    with get_session() as session:
        stats: list[SignalPerformance] = session.query(SignalPerformance).all()

        for sp in stats:
            if sp.executed_signals < _MIN_TRADES_FOR_CALIBRATION:
                logger.info(
                    f"Skipping {sp.signal_name} — only {sp.executed_signals} trades "
                    f"(need {_MIN_TRADES_FOR_CALIBRATION})"
                )
                updated_weights[sp.signal_name] = sp.signal_weight
                continue

            old_weight = sp.signal_weight
            new_weight = old_weight

            high_performer = (
                sp.win_rate >= _HIGH_WIN_RATE and sp.avg_pnl_pct >= _HIGH_AVG_PNL
            )
            low_performer = (
                sp.win_rate <= _LOW_WIN_RATE or sp.avg_pnl_pct <= _LOW_AVG_PNL
            )

            if high_performer:
                new_weight = min(_MAX_WEIGHT, old_weight + _WEIGHT_STEP)
                logger.info(
                    f"{sp.signal_name}: boosted {old_weight:.2f} → {new_weight:.2f} "
                    f"(win_rate={sp.win_rate:.0%}, avg_pnl={sp.avg_pnl_pct:+.1f}%)"
                )
            elif low_performer:
                new_weight = max(_MIN_WEIGHT, old_weight - _WEIGHT_STEP)
                logger.info(
                    f"{sp.signal_name}: reduced {old_weight:.2f} → {new_weight:.2f} "
                    f"(win_rate={sp.win_rate:.0%}, avg_pnl={sp.avg_pnl_pct:+.1f}%)"
                )

            sp.signal_weight = new_weight
            sp.last_calibrated = datetime.utcnow()
            updated_weights[sp.signal_name] = new_weight

    logger.info(f"Calibration complete: {updated_weights}")
    return updated_weights


def get_current_weights() -> dict[str, float]:
    """Load weights from DB for use in screener. Falls back to 1.0 if no data."""
    with get_session() as session:
        stats = session.query(SignalPerformance).all()
        return {sp.signal_name: sp.signal_weight for sp in stats}


if __name__ == "__main__":
    weights = run()
    print("Updated weights:", weights)
