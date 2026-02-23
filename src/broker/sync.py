"""
Broker ↔ DB reconciliation — positions and fund balance.

Runs automatically at:
  - 07:15 IST (pre-market, before the morning screen)
  - Every 60 min during market hours (piggybacked on the swing monitor)
  - 15:35 IST (post-market EOD review)
  - On demand via /fundbot sync

What it detects:
  1. Positions the user opened in the broker app without telling the Slack bot
     → creates a Position record so the bot tracks it going forward
  2. Positions the bot tracked that the user closed directly in the broker app
     → closes the Position record and calculates P&L
  3. Fund balance changes (additions or withdrawals)
     → posts a Slack alert so the bot's capital assumptions stay accurate

Both Angel One and Zerodha field names are handled; the code tries both.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from src.db.models import Position, PositionStatus, ExitReason

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from src.broker.base import BrokerBase

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Summary of every change the sync detected."""

    new_positions: list[dict] = field(
        default_factory=list
    )  # Buys done in broker, not in DB
    closed_positions: list[dict] = field(
        default_factory=list
    )  # Sells done in broker, not in DB
    fund_balance_inr: float = 0.0
    fund_change_inr: float = 0.0  # positive = added, negative = withdrawn
    errors: list[str] = field(default_factory=list)

    @property
    def has_position_changes(self) -> bool:
        return bool(self.new_positions or self.closed_positions)

    @property
    def has_fund_change(self) -> bool:
        return abs(self.fund_change_inr) > 500  # ₹500 threshold to filter out noise


def run_sync(
    broker: "BrokerBase",
    session: "Session",
    last_known_balance: float = 0.0,
) -> SyncResult:
    """
    Full broker sync — positions then funds.
    Always call this at the start of each monitor run so the DB reflects
    real broker state before any new suggestions are evaluated.
    """
    result = SyncResult()
    _sync_positions(broker, session, result)
    _sync_funds(broker, result, last_known_balance)
    return result


# ── Position sync ─────────────────────────────────────────────────────────────


def _sync_positions(
    broker: "BrokerBase",
    session: "Session",
    result: SyncResult,
) -> None:
    """Compare broker holdings/positions against open DB positions."""

    # Fetch from broker — holdings = delivery/demat; positions = open MIS/CNC
    try:
        broker_holdings = broker.get_holdings()
        broker_positions = broker.get_positions()
    except Exception as e:
        msg = f"Failed to fetch broker state: {e}"
        logger.error(msg)
        result.errors.append(msg)
        return

    # Build a unified symbol → {quantity, avg_price, ltp} map.
    # Angel One fields: tradingsymbol, authorisedquantity, averageprice, ltp
    # Zerodha fields:   tradingsymbol, quantity, average_price, last_price
    broker_map: dict[str, dict] = {}
    for raw in broker_holdings + broker_positions:
        symbol = raw.get("tradingsymbol") or raw.get("symbol", "")
        qty = int(raw.get("authorisedquantity") or raw.get("quantity") or 0)
        if not symbol or qty <= 0:
            continue
        broker_map[symbol] = {
            "quantity": qty,
            "avg_price": float(
                raw.get("averageprice") or raw.get("average_price") or 0
            ),
            "ltp": float(raw.get("ltp") or raw.get("last_price") or 0),
        }

    # All positions currently marked open in the DB
    db_open: list[Position] = (
        session.query(Position).filter(Position.status == PositionStatus.OPEN).all()
    )
    db_map: dict[str, Position] = {p.symbol: p for p in db_open}

    # ── Case 1: In broker but not in DB — external buy ────────────────────
    for symbol, bp in broker_map.items():
        if symbol in db_map:
            continue
        avg = bp["avg_price"] or bp["ltp"]
        try:
            new_pos = Position(
                symbol=symbol,
                action="BUY",
                entry_price=avg,
                # Defaults: 6% stop, 10% target — user should review and adjust
                current_stop=round(avg * 0.94, 2),
                target=round(avg * 1.10, 2),
                quantity=bp["quantity"],
                entry_date=datetime.utcnow(),
                status=PositionStatus.OPEN,
                is_externally_created=True,
            )
            session.add(new_pos)
            session.flush()
            result.new_positions.append(
                {
                    "symbol": symbol,
                    "quantity": bp["quantity"],
                    "avg_price": avg,
                    "ltp": bp["ltp"],
                }
            )
            logger.info(
                f"Sync: External buy — {symbol} ×{bp['quantity']} @ ₹{avg:,.2f}"
            )
        except Exception as e:
            msg = f"Failed to create synced position for {symbol}: {e}"
            logger.error(msg)
            result.errors.append(msg)

    # ── Case 2: In DB but gone from broker — external sell ────────────────
    for symbol, db_pos in db_map.items():
        bp = broker_map.get(symbol)
        if bp and bp["quantity"] > 0:
            continue  # Still held — nothing to do
        exit_price = (bp["ltp"] if bp else 0) or db_pos.target
        try:
            pnl_inr = (exit_price - db_pos.entry_price) * db_pos.quantity
            pnl_pct = (exit_price - db_pos.entry_price) / db_pos.entry_price * 100

            db_pos.status = PositionStatus.CLOSED
            db_pos.exit_price = exit_price
            db_pos.exit_date = datetime.utcnow()
            db_pos.exit_reason = ExitReason.MANUAL
            db_pos.pnl_inr = round(pnl_inr, 2)
            db_pos.pnl_pct = round(pnl_pct, 2)
            db_pos.held_days = (datetime.utcnow() - db_pos.entry_date).days

            result.closed_positions.append(
                {
                    "symbol": symbol,
                    "entry_price": db_pos.entry_price,
                    "exit_price": exit_price,
                    "pnl_inr": round(pnl_inr, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "held_days": db_pos.held_days,
                }
            )
            logger.info(
                f"Sync: External sell — {symbol}, "
                f"P&L ₹{pnl_inr:+,.0f} ({pnl_pct:+.1f}%)"
            )
        except Exception as e:
            msg = f"Failed to sync-close position for {symbol}: {e}"
            logger.error(msg)
            result.errors.append(msg)

    session.commit()


# ── Fund balance sync ─────────────────────────────────────────────────────────


def _sync_funds(
    broker: "BrokerBase",
    result: SyncResult,
    last_known_balance: float,
) -> None:
    """Check current available margin against last known balance."""
    try:
        balance = _fetch_balance(broker)
        result.fund_balance_inr = balance
        result.fund_change_inr = balance - last_known_balance
        if result.has_fund_change:
            direction = "added" if result.fund_change_inr > 0 else "withdrawn"
            logger.info(
                f"Sync: Funds {direction} — "
                f"₹{abs(result.fund_change_inr):,.0f} "
                f"(balance now ₹{balance:,.0f})"
            )
    except Exception as e:
        msg = f"Could not fetch fund balance: {e}"
        logger.warning(msg)
        result.errors.append(msg)


def _fetch_balance(broker: "BrokerBase") -> float:
    """Available net margin. Handles both Angel One and Zerodha."""
    # Angel One — SmartAPI rmsLimit → data.net
    if hasattr(broker, "_obj"):
        try:
            resp = broker._obj.rmsLimit()
            return float((resp.get("data") or {}).get("net", 0))
        except Exception:
            pass
    # Zerodha — margins()["equity"]["net"]
    if hasattr(broker, "_kite"):
        try:
            margins = broker._kite.margins()
            return float((margins.get("equity") or {}).get("net", 0))
        except Exception:
            pass
    return 0.0
