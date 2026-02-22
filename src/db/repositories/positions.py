"""CRUD operations for Position."""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from src.db.models import Position, PositionStatus, ExitReason


class PositionRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_external(
        self,
        symbol: str,
        action: str,
        entry_price: float,
        quantity: int,
        target: float,
        stop: float,
    ) -> Position:
        """
        Create a position detected via broker sync — opened directly in the
        broker app without a corresponding TradeSuggestion in the DB.
        Default stop/target are conservative placeholders; the user should
        update them via the Slack bot or the broker app.
        """
        position = Position(
            symbol=symbol,
            action=action,
            entry_price=entry_price,
            entry_date=datetime.utcnow(),
            quantity=quantity,
            target=target,
            current_stop=stop,
            is_externally_created=True,
        )
        self.session.add(position)
        self.session.flush()
        return position

    def create(self, suggestion_id: int, entry_price: float, quantity: int,
               symbol: str, action: str, target: float, stop: float,
               slack_thread_ts: str = "") -> Position:
        position = Position(
            suggestion_id=suggestion_id,
            symbol=symbol,
            action=action,
            entry_price=entry_price,
            entry_date=datetime.utcnow(),
            quantity=quantity,
            target=target,
            current_stop=stop,
            slack_thread_ts=slack_thread_ts,
        )
        self.session.add(position)
        self.session.flush()
        return position

    def get_open(self) -> list[Position]:
        return (
            self.session.query(Position)
            .filter(Position.status == PositionStatus.OPEN)
            .all()
        )

    def get_by_symbol(self, symbol: str) -> list[Position]:
        return (
            self.session.query(Position)
            .filter(Position.symbol == symbol, Position.status == PositionStatus.OPEN)
            .all()
        )

    def close(self, position_id: int, exit_price: float,
              reason: ExitReason) -> Position:
        p = self.session.get(Position, position_id)
        p.exit_price = exit_price
        p.exit_date = datetime.utcnow()
        p.exit_reason = reason
        p.status = PositionStatus.CLOSED
        p.held_days = (p.exit_date - p.entry_date).days

        if p.action == "BUY":
            p.pnl_inr = (exit_price - p.entry_price) * p.quantity
        else:
            p.pnl_inr = (p.entry_price - exit_price) * p.quantity

        p.pnl_pct = round(p.pnl_inr / (p.entry_price * p.quantity) * 100, 2)
        return p

    def update_stop(self, position_id: int, new_stop: float) -> Position:
        p = self.session.get(Position, position_id)
        p.current_stop = new_stop
        return p

    def get_portfolio_summary(self) -> dict:
        open_positions = self.get_open()
        total_invested = sum(p.entry_price * p.quantity for p in open_positions)
        return {
            "count": len(open_positions),
            "positions": [
                {"symbol": p.symbol, "action": p.action,
                 "qty": p.quantity, "entry": p.entry_price,
                 "stop": p.current_stop, "target": p.target}
                for p in open_positions
            ],
            "total_invested_inr": total_invested,
        }
