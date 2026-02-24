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
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY | SELL

    # Prices
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    target_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    suggested_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_amount_inr: Mapped[float] = mapped_column(Float, nullable=False)  # INR at risk
    risk_reward: Mapped[float] = mapped_column(Float, nullable=False)

    # What triggered this suggestion
    signals_fired: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )  # [{"name": "ema_crossover", "strength": 0.8, ...}]
    composite_score: Mapped[float] = mapped_column(Float, nullable=False)  # Weighted aggregate
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)  # daily | weekly

    # Market context snapshot (for post-hoc analysis)
    market_context: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON
    )  # {"nifty_trend": "up", "vix": 14.2, "sector": "bullish"}

    # Slack threading
    slack_ts: Mapped[Optional[str]] = mapped_column(
        String(50), index=True
    )  # Message timestamp (used to update/thread)
    slack_channel: Mapped[Optional[str]] = mapped_column(String(50))

    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus), default=SuggestionStatus.PENDING, nullable=False
    )
    user_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    user_notes: Mapped[Optional[str]] = mapped_column(Text)  # Optional user comment

    position: Mapped[Optional["Position"]] = relationship("Position", back_populates="suggestion", uselist=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)


class Position(Base):
    """
    A suggestion the user actually executed. Tracks the full life of a trade
    from entry to exit so the learning module can compute real outcomes.
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    suggestion_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("trade_suggestions.id"), unique=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(4), nullable=False)

    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Updated as trade progresses
    current_stop: Mapped[Optional[float]] = mapped_column(Float)  # May be trailed up/down
    target: Mapped[Optional[float]] = mapped_column(Float)
    trailing_stop: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)

    # Set on close
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    exit_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    exit_reason: Mapped[Optional[ExitReason]] = mapped_column(Enum(ExitReason))
    pnl_inr: Mapped[Optional[float]] = mapped_column(Float)
    pnl_pct: Mapped[Optional[float]] = mapped_column(Float)
    held_days: Mapped[Optional[int]] = mapped_column(Integer)

    status: Mapped[PositionStatus] = mapped_column(Enum(PositionStatus), default=PositionStatus.OPEN, nullable=False)

    # Slack thread for updates
    slack_thread_ts: Mapped[Optional[str]] = mapped_column(String(50))

    # True when the position was detected via broker sync — the user opened
    # this trade directly in the broker app without going through the bot.
    is_externally_created: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)

    suggestion: Mapped[Optional["TradeSuggestion"]] = relationship("TradeSuggestion", back_populates="position")
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)

    # Rolling counters (last 90 days, updated incrementally)
    total_signals: Mapped[int] = mapped_column(Integer, default=0)
    executed_signals: Mapped[int] = mapped_column(Integer, default=0)  # How many were actually traded
    winning_trades: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)  # winning / executed
    avg_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_risk_reward: Mapped[float] = mapped_column(Float, default=0.0)
    avg_held_days: Mapped[float] = mapped_column(Float, default=0.0)

    # Composite quality score  (0–1), used to adjust weight in screener
    signal_weight: Mapped[float] = mapped_column(Float, default=1.0)

    last_calibrated: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DailyJournal(Base):
    """
    One row per trading day — the bot's daily diary.
    Pre-market section filled at 07:30, swing monitor updates live,
    post-market section filled after 15:30.
    """

    __tablename__ = "daily_journal"
    __table_args__ = (UniqueConstraint("date", name="uq_journal_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    # Pre-market (07:30 AM)
    nifty_trend: Mapped[Optional[str]] = mapped_column(String(20))  # bullish | bearish | sideways
    vix_level: Mapped[Optional[float]] = mapped_column(Float)
    sgx_nifty_gap: Mapped[Optional[float]] = mapped_column(Float)  # Overnight gap %
    key_levels: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)  # {"support": 22100, "resistance": 22400}
    watchlist_snapshot: Mapped[Optional[list[str]]] = mapped_column(JSON)  # Stocks flagged for swing setups today
    pre_market_summary: Mapped[Optional[str]] = mapped_column(Text)  # Human-readable brief

    # Live (updated each hourly monitor run)
    suggestions_sent: Mapped[int] = mapped_column(Integer, default=0)
    suggestions_executed: Mapped[int] = mapped_column(Integer, default=0)
    suggestions_skipped: Mapped[int] = mapped_column(Integer, default=0)

    # Broker sync tracking
    fund_balance_inr: Mapped[float] = mapped_column(Float, default=0.0)  # Available margin at last sync
    fund_added_inr: Mapped[float] = mapped_column(Float, default=0.0)  # Net funds added today
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # Timestamp of most recent broker sync

    # Post-market (15:35 PM)
    total_pnl_inr: Mapped[Optional[float]] = mapped_column(Float)
    total_pnl_pct: Mapped[Optional[float]] = mapped_column(Float)
    open_positions_count: Mapped[int] = mapped_column(Integer, default=0)
    post_market_review: Mapped[Optional[str]] = mapped_column(Text)  # What worked, what didn't

    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
