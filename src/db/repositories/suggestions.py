"""CRUD operations for TradeSuggestion."""

from datetime import datetime, date
from typing import Optional

from sqlalchemy.orm import Session

from src.db.models import TradeSuggestion, SuggestionStatus


class SuggestionRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, **kwargs) -> TradeSuggestion:
        suggestion = TradeSuggestion(**kwargs)
        self.session.add(suggestion)
        self.session.flush()
        return suggestion

    def get_by_id(self, suggestion_id: int) -> Optional[TradeSuggestion]:
        return self.session.get(TradeSuggestion, suggestion_id)

    def get_by_slack_ts(self, slack_ts: str) -> Optional[TradeSuggestion]:
        return (
            self.session.query(TradeSuggestion)
            .filter(TradeSuggestion.slack_ts == slack_ts)
            .first()
        )

    def get_pending_today(self) -> list[TradeSuggestion]:
        today = date.today()
        return (
            self.session.query(TradeSuggestion)
            .filter(
                TradeSuggestion.status == SuggestionStatus.PENDING,
                TradeSuggestion.date >= datetime.combine(today, datetime.min.time()),
            )
            .all()
        )

    def mark_executed(self, suggestion_id: int, notes: str = "") -> TradeSuggestion:
        s = self.get_by_id(suggestion_id)
        assert s is not None, f"TradeSuggestion {suggestion_id} not found"
        s.status = SuggestionStatus.EXECUTED
        s.user_response_at = datetime.utcnow()
        s.user_notes = notes
        return s

    def mark_skipped(self, suggestion_id: int, notes: str = "") -> TradeSuggestion:
        s = self.get_by_id(suggestion_id)
        assert s is not None, f"TradeSuggestion {suggestion_id} not found"
        s.status = SuggestionStatus.SKIPPED
        s.user_response_at = datetime.utcnow()
        s.user_notes = notes
        return s

    def expire_stale(self) -> int:
        """Mark all pending suggestions from previous days as expired."""
        today = date.today()
        result = (
            self.session.query(TradeSuggestion)
            .filter(
                TradeSuggestion.status == SuggestionStatus.PENDING,
                TradeSuggestion.date < datetime.combine(today, datetime.min.time()),
            )
            .update({"status": SuggestionStatus.EXPIRED})
        )
        return result
