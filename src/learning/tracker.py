"""
Outcome tracker — called whenever a position closes.

For each signal that contributed to the trade, records:
  - whether it was executed
  - the P&L outcome
  - the actual R:R achieved
  - how many days the trade was held

This data feeds the calibrator which adjusts signal weights.
"""

import logging

from sqlalchemy.orm import Session

from src.db.models import Position, TradeSuggestion
from src.db.repositories.performance import PerformanceRepository

logger = logging.getLogger(__name__)


class OutcomeTracker:
    def __init__(self, session: Session):
        self.session = session
        self.perf_repo = PerformanceRepository(session)

    def record_close(self, position: Position) -> None:
        """
        Called immediately after a position is closed (by post_market pipeline
        or by the confirm_close Slack action).
        Updates SignalPerformance for every signal that contributed to this trade.
        """
        if not position.suggestion_id:
            return

        suggestion: TradeSuggestion = (
            self.session.query(TradeSuggestion)
            .filter(TradeSuggestion.id == position.suggestion_id)
            .first()
        )
        if not suggestion or not suggestion.signals_fired:
            return

        pnl_pct = position.pnl_pct or 0.0
        held_days = position.held_days or 0

        # Actual R:R = realised profit / risk taken
        risk = abs(suggestion.entry_price - suggestion.stop_loss)
        realised = abs(
            (position.exit_price or suggestion.entry_price) - suggestion.entry_price
        )
        actual_rr = round(realised / risk, 2) if risk > 0 else 0.0

        for signal_dict in suggestion.signals_fired:
            signal_name = signal_dict.get("signal_name")
            timeframe = signal_dict.get("timeframe", suggestion.timeframe)
            if not signal_name:
                continue
            try:
                self.perf_repo.record_signal_outcome(
                    signal_name=signal_name,
                    timeframe=timeframe,
                    was_executed=True,
                    pnl_pct=pnl_pct,
                    risk_reward=actual_rr,
                    held_days=held_days,
                )
                logger.debug(
                    f"Recorded outcome for {signal_name}: "
                    f"P&L {pnl_pct:+.1f}%, R:R {actual_rr}, held {held_days}d"
                )
            except Exception as e:
                logger.error(f"Failed to record outcome for signal {signal_name}: {e}")

    def record_skipped(self, suggestion: TradeSuggestion) -> None:
        """
        When a suggestion is skipped, still count it as a signal fired
        (so the denominator for execution rate is accurate).
        """
        for signal_dict in suggestion.signals_fired or []:
            signal_name = signal_dict.get("signal_name")
            timeframe = signal_dict.get("timeframe", suggestion.timeframe)
            if not signal_name:
                continue
            try:
                sp = self.perf_repo.get_or_create_signal(signal_name, timeframe)
                sp.total_signals += 1
                # Not executed, so win metrics unchanged
            except Exception as e:
                logger.error(f"Failed to record skipped for {signal_name}: {e}")
