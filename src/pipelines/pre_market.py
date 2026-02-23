"""
Pre-market pipeline  —  runs at 07:30 AM IST every trading day.

What it does:
  1. Fetches Nifty 50 trend, VIX, and SGX Nifty gap
  2. Runs full watchlist screen (Nifty 50 + Midcap 50)
  3. Identifies key support/resistance levels for today
  4. Builds a prioritised watchlist for intraday monitoring
  5. Posts a morning briefing to Slack
  6. Persists setup in DailyJournal
"""

import logging
from datetime import datetime, timedelta

import pandas_ta as ta

from src.broker import get_broker
from src.analysis.screener import Screener, ScreenerResult
from src.db.connection import get_session
from src.db.repositories.performance import PerformanceRepository
from src.db.repositories.suggestions import SuggestionRepository

logger = logging.getLogger(__name__)


def _assess_nifty_trend(broker) -> tuple[str, float]:
    """
    Returns (trend_direction, vix_level).
    trend: "bullish" | "bearish" | "sideways"
    """
    from datetime import datetime

    try:
        df = broker.get_historical_data(
            "NIFTY 50",
            interval="day",
            from_date=datetime.now() - timedelta(days=60),
            to_date=datetime.now(),
            exchange="NSE",
        )
        if df.empty:
            return "unknown", 0.0

        df["ema20"] = ta.ema(df["close"], length=20)
        df["ema50"] = ta.ema(df["close"], length=50)
        last = df.iloc[-1]

        if last["close"] > last["ema20"] > last["ema50"]:
            trend = "bullish"
        elif last["close"] < last["ema20"] < last["ema50"]:
            trend = "bearish"
        else:
            trend = "sideways"
        return trend, 0.0  # VIX fetched separately
    except Exception as e:
        logger.warning(f"Failed to assess Nifty trend: {e}")
        return "unknown", 0.0


def _get_vix(broker) -> float:
    try:
        quotes = broker.get_quote(["INDIA VIX"], exchange="NSE")
        return quotes.get("INDIA VIX", None)
        if quotes:
            return list(quotes.values())[0].last_price
        return 0.0
    except Exception:
        return 0.0


def _build_morning_brief(
    trend: str, vix: float, top_setups: list[ScreenerResult]
) -> str:
    """Format the Slack morning briefing message."""
    trend_emoji = {
        "bullish": ":chart_with_upwards_trend:",
        "bearish": ":chart_with_downwards_trend:",
        "sideways": ":left_right_arrow:",
    }.get(trend, ":question:")

    lines = [
        f"*Good morning! Pre-market brief — {datetime.now().strftime('%d %b %Y')}*",
        "",
        f"{trend_emoji} *Market setup:* Nifty trend is *{trend.upper()}*  |  VIX: *{vix:.1f}*",
        "",
        f"*Top {len(top_setups)} setups identified for today:*",
    ]

    for i, setup in enumerate(top_setups, 1):
        direction_emoji = (
            ":green_circle:" if setup.direction == "BUY" else ":red_circle:"
        )
        signals = ", ".join(s["signal_name"] for s in setup.signals_fired)
        lines.append(
            f"{i}. {direction_emoji} *{setup.symbol}*  "
            f"Entry: ₹{setup.entry}  Target: ₹{setup.target}  "
            f"SL: ₹{setup.stop_loss}  R:R {setup.risk_reward}x  "
            f"[{signals}]  Score: {setup.composite_score:.2f}"
        )

    lines += [
        "",
        "_Intraday monitoring active. Signals will be sent when price approaches entry zones._",
        "_Respond to each signal with ✅ Execute or ⏭️ Skip._",
    ]

    return "\n".join(lines)


def run():
    """Entry point — called by scheduler or Lambda."""
    logger.info("Starting pre-market pipeline")
    broker = get_broker()

    # 1. Market context
    trend, vix = _assess_nifty_trend(broker)
    logger.info(f"Nifty trend: {trend}, VIX: {vix}")

    # 2. Screen full watchlist
    screener = Screener(broker)
    all_setups = screener.run()

    # 3. Take top 10 for watchlist, top 5 for briefing
    top_10 = all_setups[:10]
    top_5 = all_setups[:5]

    # 4. Persist to daily journal
    with get_session() as session:
        perf_repo = PerformanceRepository(session)
        sugg_repo = SuggestionRepository(session)

        # Expire any stale suggestions from yesterday
        expired = sugg_repo.expire_stale()
        if expired:
            logger.info(f"Expired {expired} stale suggestions from previous days")

        perf_repo.update_pre_market(
            nifty_trend=trend,
            vix=vix,
            gap_pct=0.0,  # SGX Nifty gap — TODO: add SGX data source
            key_levels={},
            watchlist=[s.symbol for s in top_10],
            summary=f"Nifty {trend}, VIX {vix:.1f}, {len(all_setups)} setups found",
        )

    # 5. Post briefing to Slack (Slack handler reads from DB and sends)
    brief = _build_morning_brief(trend, vix, top_5)
    logger.info(f"Pre-market briefing prepared: {len(top_5)} setups")
    return {
        "brief": brief,
        "watchlist": [s.symbol for s in top_10],
        "setups": all_setups,
        "trend": trend,
        "vix": vix,
    }


if __name__ == "__main__":
    import structlog

    structlog.configure()
    run()
