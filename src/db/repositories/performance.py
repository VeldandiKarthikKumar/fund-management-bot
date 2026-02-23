"""CRUD operations for SignalPerformance and DailyJournal."""

from datetime import datetime, date

from sqlalchemy.orm import Session

from src.db.models import DailyJournal, SignalPerformance


class PerformanceRepository:
    def __init__(self, session: Session):
        self.session = session

    # ── SignalPerformance ──────────────────────────────────────────────────

    def get_or_create_signal(
        self, signal_name: str, timeframe: str
    ) -> SignalPerformance:
        sp = (
            self.session.query(SignalPerformance)
            .filter_by(signal_name=signal_name, timeframe=timeframe)
            .first()
        )
        if not sp:
            sp = SignalPerformance(signal_name=signal_name, timeframe=timeframe)
            self.session.add(sp)
            self.session.flush()
        return sp

    def record_signal_outcome(
        self,
        signal_name: str,
        timeframe: str,
        was_executed: bool,
        pnl_pct: float,
        risk_reward: float,
        held_days: int,
    ) -> SignalPerformance:
        sp = self.get_or_create_signal(signal_name, timeframe)
        sp.total_signals += 1
        if was_executed:
            sp.executed_signals += 1
            if pnl_pct > 0:
                sp.winning_trades += 1
            # Rolling averages (exponential smoothing, alpha=0.1)
            alpha = 0.1
            sp.avg_pnl_pct = (1 - alpha) * sp.avg_pnl_pct + alpha * pnl_pct
            sp.avg_risk_reward = (1 - alpha) * sp.avg_risk_reward + alpha * risk_reward
            sp.avg_held_days = (1 - alpha) * sp.avg_held_days + alpha * held_days

        if sp.executed_signals > 0:
            sp.win_rate = sp.winning_trades / sp.executed_signals
        sp.updated_at = datetime.utcnow()
        return sp

    def get_all_signal_stats(self) -> list[SignalPerformance]:
        return self.session.query(SignalPerformance).all()

    # ── DailyJournal ──────────────────────────────────────────────────────

    def get_or_create_today(self) -> DailyJournal:
        today = date.today()
        journal = (
            self.session.query(DailyJournal)
            .filter(DailyJournal.date >= datetime.combine(today, datetime.min.time()))
            .first()
        )
        if not journal:
            journal = DailyJournal(date=datetime.combine(today, datetime.min.time()))
            self.session.add(journal)
            self.session.flush()
        return journal

    def update_pre_market(
        self,
        nifty_trend: str,
        vix: float,
        gap_pct: float,
        key_levels: dict,
        watchlist: list,
        summary: str,
    ) -> DailyJournal:
        journal = self.get_or_create_today()
        journal.nifty_trend = nifty_trend
        journal.vix_level = vix
        journal.sgx_nifty_gap = gap_pct
        journal.key_levels = key_levels
        journal.watchlist_snapshot = watchlist
        journal.pre_market_summary = summary
        return journal

    def increment_suggestion_count(self, executed: bool = False, skipped: bool = False):
        journal = self.get_or_create_today()
        journal.suggestions_sent += 1
        if executed:
            journal.suggestions_executed += 1
        if skipped:
            journal.suggestions_skipped += 1

    def update_post_market(
        self, pnl_inr: float, pnl_pct: float, open_positions: int, review: str
    ) -> DailyJournal:
        journal = self.get_or_create_today()
        journal.total_pnl_inr = pnl_inr
        journal.total_pnl_pct = pnl_pct
        journal.open_positions_count = open_positions
        journal.post_market_review = review
        return journal
