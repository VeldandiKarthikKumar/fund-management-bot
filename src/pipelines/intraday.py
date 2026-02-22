"""
Swing position monitor  —  runs hourly during market hours (09:15–15:15 IST).

For swing trading we do NOT need 15-minute tick-chasing. What matters:
  1. Sync broker account first — catch trades the user did directly in the app
  2. Alert if a watched setup has entered its limit-order entry zone (±1.5%)
  3. Alert on positions that have hit their stop or target
  4. Persist any fund balance change so capital assumptions stay current

Entry zone is deliberately wide (±1.5%) — swing entries are placed as limit
orders, not market orders. The bot suggests the zone; the user sets the order.
"""
import logging
from datetime import datetime

from src.broker import get_broker
from src.broker.sync import run_sync
from src.analysis.screener import Screener
from src.config import get_settings
from src.db.connection import get_session
from src.db.repositories.performance import PerformanceRepository
from src.db.repositories.positions import PositionRepository
from src.db.repositories.suggestions import SuggestionRepository

logger = logging.getLogger(__name__)

# Swing entry zone: alert when price is within ±1.5% of the target entry price.
# Wide enough for limit-order fills across a session; not a scalper's tick range.
_ENTRY_ZONE_TOLERANCE = 0.015


def _price_in_entry_zone(current_price: float, entry: float) -> bool:
    return abs(current_price - entry) / entry <= _ENTRY_ZONE_TOLERANCE


def _check_position_exits(pos_repo: PositionRepository, quotes: dict) -> list[dict]:
    """
    Returns exit alerts for positions that have crossed stop or target.
    For swing trades we alert and let the user decide — positions are not
    auto-closed (the user executes in the broker app).
    """
    alerts = []
    for pos in pos_repo.get_open():
        quote = quotes.get(pos.symbol)
        if not quote:
            continue

        price = quote.last_price
        hit_stop = (
            (pos.action == "BUY"  and price <= pos.current_stop) or
            (pos.action == "SELL" and price >= pos.current_stop)
        )
        hit_target = (
            (pos.action == "BUY"  and price >= pos.target) or
            (pos.action == "SELL" and price <= pos.target)
        )

        if hit_stop or hit_target:
            alerts.append({
                "position_id":     pos.id,
                "symbol":          pos.symbol,
                "action":          pos.action,
                "current_price":   price,
                "entry_price":     pos.entry_price,
                "stop":            pos.current_stop,
                "target":          pos.target,
                "reason":          "target_hit" if hit_target else "stop_hit",
                "slack_thread_ts": pos.slack_thread_ts,
            })
    return alerts


def run() -> dict:
    """Entry point — called by the scheduler every hour during market hours."""
    now = datetime.now()
    logger.info(f"Swing monitor at {now.strftime('%H:%M')}")

    broker   = get_broker()
    settings = get_settings()

    with get_session() as session:
        perf_repo = PerformanceRepository(session)
        pos_repo  = PositionRepository(session)
        sugg_repo = SuggestionRepository(session)

        # ── 1. Broker sync — always first ───────────────────────────────────
        # Reconciles any trades or fund moves the user did directly in the app.
        journal      = perf_repo.get_or_create_today()
        last_balance = journal.fund_balance_inr or 0.0
        sync_result  = run_sync(broker, session, last_known_balance=last_balance)

        if sync_result.fund_balance_inr:
            journal.fund_balance_inr = sync_result.fund_balance_inr
            journal.fund_added_inr   = (journal.fund_added_inr or 0.0) + max(0.0, sync_result.fund_change_inr)
            journal.last_sync_at     = datetime.utcnow()

        # ── 2. Live quotes ───────────────────────────────────────────────────
        watchlist = journal.watchlist_snapshot or []
        if not watchlist:
            logger.info("No pre-market watchlist found; running quick screen")
            screener  = Screener(broker)
            setups    = screener.run()
            watchlist = [s.symbol for s in setups[:10]]

        try:
            quotes = broker.get_quote(watchlist)
        except Exception as e:
            logger.error(f"Failed to get live quotes: {e}")
            return {"error": str(e), "sync": sync_result}

        # ── 3. Exit alerts ───────────────────────────────────────────────────
        exit_alerts = _check_position_exits(pos_repo, quotes)

        # ── 4. New swing setups entering entry zone ──────────────────────────
        open_count      = len(pos_repo.get_open())
        new_suggestions = []

        if open_count < settings.max_open_positions:
            screener = Screener(broker)
            setups   = screener.run(symbols=watchlist)

            already_suggested = {s.symbol for s in sugg_repo.get_pending_today()}

            for setup in setups:
                if setup.symbol in already_suggested:
                    continue
                quote = quotes.get(setup.symbol)
                if not quote:
                    continue
                if _price_in_entry_zone(quote.last_price, setup.entry):
                    qty = broker.compute_quantity(
                        capital=settings.fund_size_inr,
                        entry=setup.entry,
                        stop=setup.stop_loss,
                        risk_pct=settings.max_risk_per_trade_pct / 100,
                    )
                    new_suggestions.append({
                        "setup":    setup,
                        "quantity": qty,
                        "risk_inr": round(abs(setup.entry - setup.stop_loss) * qty, 2),
                    })
                    already_suggested.add(setup.symbol)
                    if len(new_suggestions) + open_count >= settings.max_open_positions:
                        break

        logger.info(
            f"Monitor: sync_changes={sync_result.has_position_changes}, "
            f"exit_alerts={len(exit_alerts)}, new_signals={len(new_suggestions)}, "
            f"open={open_count}/{settings.max_open_positions}"
        )

        return {
            "exit_alerts":     exit_alerts,
            "new_suggestions": new_suggestions,
            "open_positions":  open_count,
            "sync":            sync_result,
            "quotes":          {s: q.last_price for s, q in quotes.items()},
        }


if __name__ == "__main__":
    result = run()
    print(result)
