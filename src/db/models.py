"""
SQLAlchemy ORM models — the persistent state of the bot.

Tables:
  trade_suggestions  — every signal the bot sends to Slack
  positions          — confirmed executions and their outcomes
  signal_performance — rolling win-rate stats per signal type (drives learning)
  daily_journal      — one row per trading day; pre/intra/post-market notes
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Enums ─────────────────────────────────────────────────────────────────


class SuggestionStatus(str, enum.Enum):
    PENDING = "pending"  # Sent to Slack, awaiting user action
    EXECUTED = "executed"  # User confirmed they bought/sold
    SKIPPED = "skipped"  # User passed on this one
    EXPIRED = "expired"  # Market moved; signal no longer valid


class PositionStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class ExitReason(str, enum.Enum):
    TARGET_HIT = "target_hit"
    STOP_HIT = "stop_hit"
    MANUAL = "manual"  # User closed early
    TRAILING = "trailing"  # Trailing stop hit
    EXPIRED = "expired"  # Held past allowed duration


# ── Models ────────────────────────────────────────────────────────────────


class TradeSuggestion(Base):
    """
    Every signal the bot sends to Slack.  Includes the full snapshot of market
    conditions at the time so outcomes can be attributed to specific signals.
    """

    __tablename__ = "trade_suggestions"

    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False, default=datetime.utcnow)
    symbol = Column(String(20), nullable=False, index=True)
    action = Column(String(4), nullable=False)  # BUY | SELL

    # Prices
    entry_price = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    suggested_qty = Column(Integer, nullable=False)
    risk_amount_inr = Column(Float, nullable=False)  # INR at risk
    risk_reward = Column(Float, nullable=False)

    # What triggered this suggestion
    signals_fired = Column(
        JSON, nullable=False
    )  # [{"name": "ema_crossover", "strength": 0.8, ...}]
    composite_score = Column(Float, nullable=False)  # Weighted aggregate
    timeframe = Column(String(10), nullable=False)  # daily | weekly

    # Market context snapshot (for post-hoc analysis)
    market_context = Column(
        JSON
    )  # {"nifty_trend": "up", "vix": 14.2, "sector": "bullish"}

    # Slack threading
    slack_ts = Column(
        String(50), index=True
    )  # Message timestamp (used to update/thread)
    slack_channel = Column(String(50))

    status = Column(
        Enum(SuggestionStatus), default=SuggestionStatus.PENDING, nullable=False
    )
    user_response_at = Column(DateTime)
    user_notes = Column(Text)  # Optional user comment

    position = relationship("Position", back_populates="suggestion", uselist=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Position(Base):
    """
    A suggestion the user actually executed. Tracks the full life of a trade
    from entry to exit so the learning module can compute real outcomes.
    """

    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    suggestion_id = Column(Integer, ForeignKey("trade_suggestions.id"), unique=True)
    symbol = Column(String(20), nullable=False, index=True)
    action = Column(String(4), nullable=False)

    entry_price = Column(Float, nullable=False)
    entry_date = Column(DateTime, nullable=False)
    quantity = Column(Integer, nullable=False)

    # Updated as trade progresses
    current_stop = Column(Float)  # May be trailed up/down
    target = Column(Float)
    trailing_stop = Column(Boolean, default=False)

    # Set on close
    exit_price = Column(Float)
    exit_date = Column(DateTime)
    exit_reason = Column(Enum(ExitReason))
    pnl_inr = Column(Float)
    pnl_pct = Column(Float)
    held_days = Column(Integer)

    status = Column(Enum(PositionStatus), default=PositionStatus.OPEN, nullable=False)

    # Slack thread for updates
    slack_thread_ts = Column(String(50))

    # True when the position was detected via broker sync — the user opened
    # this trade directly in the broker app without going through the bot.
    is_externally_created = Column(Boolean, default=False)

    suggestion = relationship("TradeSuggestion", back_populates="position")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SignalPerformance(Base):
    """
    Rolling statistics per signal type and timeframe.
    The learning module updates this after each trade closes
    and the calibrator adjusts signal_weight accordingly.
    """

    __tablename__ = "signal_performance"
    __table_args__ = (
        UniqueConstraint("signal_name", "timeframe", name="uq_signal_timeframe"),
    )

    id = Column(Integer, primary_key=True)
    signal_name = Column(String(50), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)  # daily | weekly

    # Rolling counters (last 90 days, updated incrementally)
    total_signals = Column(Integer, default=0)
    executed_signals = Column(Integer, default=0)  # How many were actually traded
    winning_trades = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)  # winning / executed
    avg_pnl_pct = Column(Float, default=0.0)
    avg_risk_reward = Column(Float, default=0.0)
    avg_held_days = Column(Float, default=0.0)

    # Composite quality score  (0–1), used to adjust weight in screener
    signal_weight = Column(Float, default=1.0)

    last_calibrated = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DailyJournal(Base):
    """
    One row per trading day — the bot's daily diary.
    Pre-market section filled at 07:30, swing monitor updates live,
    post-market section filled after 15:30.
    """

    __tablename__ = "daily_journal"
    __table_args__ = (UniqueConstraint("date", name="uq_journal_date"),)

    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False, index=True)

    # Pre-market (07:30 AM)
    nifty_trend = Column(String(20))  # bullish | bearish | sideways
    vix_level = Column(Float)
    sgx_nifty_gap = Column(Float)  # Overnight gap %
    key_levels = Column(JSON)  # {"support": 22100, "resistance": 22400}
    watchlist_snapshot = Column(JSON)  # Stocks flagged for swing setups today
    pre_market_summary = Column(Text)  # Human-readable brief

    # Live (updated each hourly monitor run)
    suggestions_sent = Column(Integer, default=0)
    suggestions_executed = Column(Integer, default=0)
    suggestions_skipped = Column(Integer, default=0)

    # Broker sync tracking
    fund_balance_inr = Column(Float, default=0.0)  # Available margin at last sync
    fund_added_inr = Column(Float, default=0.0)  # Net funds added today
    last_sync_at = Column(DateTime)  # Timestamp of most recent broker sync

    # Post-market (15:35 PM)
    total_pnl_inr = Column(Float)
    total_pnl_pct = Column(Float)
    open_positions_count = Column(Integer, default=0)
    post_market_review = Column(Text)  # What worked, what didn't

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
