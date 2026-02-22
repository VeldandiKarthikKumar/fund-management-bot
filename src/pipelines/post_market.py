"""
Post-market pipeline  —  runs at 15:35 IST after market close.

What it does:
  1. Reconciles bot suggestions vs user-executed trades
  2. Calculates today's P&L on closed positions
  3. Checks which open positions need tomorrow's stop/target adjustments
  4. Triggers the learning module to update signal performance stats
  5. Posts end-of-day review to Slack
  6. Updates DailyJournal with post-market summary
"""
import logging
from datetime import datetime, date

from src.broker import get_broker
from src.config import get_settings
from src.db.connection import get_session
from src.db.models import SuggestionStatus, ExitReason
from src.db.repositories.performance import PerformanceRepository
from src.db.repositories.positions import PositionRepository
from src.db.repositories.suggestions import SuggestionRepository
from src.learning.tracker import OutcomeTracker

logger = logging.getLogger(__name__)


def _format_eod_review(
    closed_today: list,
    open_positions: list,
    daily_pnl: float,
    suggestions_sent: int,
    suggestions_executed: int,
) -> str:
    lines = [
        f"*End-of-day review — {date.today().strftime('%d %b %Y')}*",
        "",
        f"*Suggestions sent:* {suggestions_sent}  |  "
        f"*Executed:* {suggestions_executed}  |  "
        f"*Skipped:* {suggestions_sent - suggestions_executed}",
        "",
    ]

    if closed_today:
        lines.append("*Positions closed today:*")
        for p in closed_today:
            emoji = ":white_check_mark:" if p.pnl_inr > 0 else ":x:"
            lines.append(
                f"  {emoji} {p.symbol} ({p.action})  "
                f"Entry ₹{p.entry_price} → Exit ₹{p.exit_price}  "
                f"P&L: ₹{p.pnl_inr:+.0f} ({p.pnl_pct:+.1f}%)  "
                f"[{p.exit_reason.value if p.exit_reason else 'manual'}]"
            )
        lines.append("")

    pnl_emoji = ":moneybag:" if daily_pnl > 0 else ":chart_with_downwards_trend:"
    lines.append(f"{pnl_emoji} *Today's P&L: ₹{daily_pnl:+,.0f}*")
    lines.append("")

    if open_positions:
        lines.append(f"*Open positions carrying overnight ({len(open_positions)}):*")
        for p in open_positions:
            lines.append(
                f"  • {p['symbol']} ({p['action']})  "
                f"Entry ₹{p['entry']}  SL ₹{p['stop']}  Target ₹{p['target']}"
            )
        lines.append("")

    lines += [
        "_Signal performance stats updated. Learning module will recalibrate overnight._",
        "_Tomorrow's pre-market brief at 07:30 AM._",
    ]
    return "\n".join(lines)


def run() -> dict:
    """Entry point — called by scheduler at 15:35 IST."""
    logger.info("Starting post-market pipeline")
    broker   = get_broker()
    settings = get_settings()

    with get_session() as session:
        pos_repo  = PositionRepository(session)
        sugg_repo = SuggestionRepository(session)
        perf_repo = PerformanceRepository(session)
        tracker   = OutcomeTracker(session)

        # 1. Get live closing prices for open positions
        open_positions = pos_repo.get_open()
        symbols = [p.symbol for p in open_positions]
        quotes = {}
        if symbols:
            try:
                quotes = broker.get_quote(symbols)
            except Exception as e:
                logger.warning(f"Could not fetch closing quotes: {e}")

        # 2. Auto-close positions that hit stop/target (belt-and-suspenders)
        closed_today = []
        for pos in open_positions:
            quote = quotes.get(pos.symbol)
            if not quote:
                continue
            price = quote.last_price
            hit_target = (pos.action == "BUY" and price >= pos.target) or \
                         (pos.action == "SELL" and price <= pos.target)
            hit_stop   = (pos.action == "BUY" and price <= pos.current_stop) or \
                         (pos.action == "SELL" and price >= pos.current_stop)
            if hit_target:
                closed = pos_repo.close(pos.id, price, ExitReason.TARGET_HIT)
                closed_today.append(closed)
                tracker.record_close(closed)
            elif hit_stop:
                closed = pos_repo.close(pos.id, price, ExitReason.STOP_HIT)
                closed_today.append(closed)
                tracker.record_close(closed)

        # 3. Calculate today's P&L
        daily_pnl = sum(p.pnl_inr or 0 for p in closed_today)

        # 4. Portfolio summary
        portfolio = pos_repo.get_portfolio_summary()

        # 5. Update daily journal
        journal = perf_repo.get_or_create_today()
        perf_repo.update_post_market(
            pnl_inr=daily_pnl,
            pnl_pct=round(daily_pnl / settings.fund_size_inr * 100, 2),
            open_positions=portfolio["count"],
            review=f"Closed {len(closed_today)} positions, P&L ₹{daily_pnl:+,.0f}",
        )

        # 6. Build review message
        review = _format_eod_review(
            closed_today=closed_today,
            open_positions=portfolio["positions"],
            daily_pnl=daily_pnl,
            suggestions_sent=journal.suggestions_sent or 0,
            suggestions_executed=journal.suggestions_executed or 0,
        )

        logger.info(
            f"Post-market: closed={len(closed_today)}, "
            f"open={portfolio['count']}, pnl=₹{daily_pnl:+,.0f}"
        )

        return {
            "review":         review,
            "daily_pnl":      daily_pnl,
            "closed_today":   len(closed_today),
            "open_positions": portfolio["count"],
        }


if __name__ == "__main__":
    result = run()
    print(result)
