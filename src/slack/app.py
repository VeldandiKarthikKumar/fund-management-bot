"""
Slack Bolt app — the user-facing interface of the fund management bot.

Runs in Socket Mode (no public URL needed for development).
For production, switch to HTTP mode behind API Gateway.

Responsibilities:
  - Receive and dispatch slash commands (/fundbot status|positions|sync|stats|help)
  - Handle interactive message button callbacks (Execute / Skip / Closed / Holding)
  - Schedule and dispatch all market pipelines + broker sync jobs
  - Send formatted swing-trade suggestions, exit alerts, and sync notifications

Schedule (IST, Mon–Fri):
  07:15  — Pre-market broker sync (catch overnight corporate actions / fund adds)
  07:30  — Pre-market pipeline (Nifty assessment + full swing screen)
  09:15, 10:15, 11:15, 12:15, 13:15, 14:15, 15:15  — Hourly swing monitor
  15:35  — Post-market pipeline (EOD reconciliation + learning)
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.config import get_settings
from src.db.connection import create_tables
from src.slack.handlers.commands import register_commands
from src.slack.handlers.suggestions import register_suggestion_actions
from src.slack.handlers.positions import register_position_actions
from src.slack import notifier

logger = logging.getLogger(__name__)
settings = get_settings()

app = App(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)

# ── Register all handlers ─────────────────────────────────────────────────
register_commands(app)
register_suggestion_actions(app)
register_position_actions(app)


# ── Pipeline functions ────────────────────────────────────────────────────

def _run_broker_sync():
    """Standalone broker sync — runs before the morning screen and on demand."""
    from src.broker import get_broker
    from src.broker.sync import run_sync
    from src.db.connection import get_session
    from src.db.repositories.performance import PerformanceRepository
    try:
        broker = get_broker()
        with get_session() as session:
            perf_repo    = PerformanceRepository(session)
            journal      = perf_repo.get_or_create_today()
            last_balance = journal.fund_balance_inr or 0.0
            result       = run_sync(broker, session, last_known_balance=last_balance)
            if result.fund_balance_inr:
                journal.fund_balance_inr = result.fund_balance_inr
                journal.fund_added_inr   = (journal.fund_added_inr or 0.0) + max(0.0, result.fund_change_inr)
        if result.has_position_changes or result.has_fund_change:
            notifier.post_sync_alert(app.client, result)
    except Exception as e:
        logger.error(f"Broker sync failed: {e}", exc_info=True)


def _run_pre_market():
    from src.pipelines.pre_market import run
    try:
        result = run()
        notifier.post_pre_market_brief(app.client, result["brief"])
        notifier.post_suggestions(app.client, result["setups"][:5], result)
    except Exception as e:
        logger.error(f"Pre-market pipeline failed: {e}", exc_info=True)
        notifier.post_error(app.client, f"Pre-market pipeline error: {e}")


def _run_swing_monitor():
    """Hourly swing monitor — broker sync + entry alerts + exit alerts."""
    from src.pipelines.intraday import run
    try:
        result = run()
        # Post sync alert if the broker sync found external changes
        sync = result.get("sync")
        if sync and (sync.has_position_changes or sync.has_fund_change):
            notifier.post_sync_alert(app.client, sync)
        for alert in result.get("exit_alerts", []):
            notifier.post_exit_alert(app.client, alert)
        for suggestion in result.get("new_suggestions", []):
            notifier.post_trade_suggestion(app.client, suggestion)
    except Exception as e:
        logger.error(f"Swing monitor failed: {e}", exc_info=True)


def _run_post_market():
    from src.pipelines.post_market import run
    try:
        result = run()
        notifier.post_eod_review(app.client, result["review"])
    except Exception as e:
        logger.error(f"Post-market pipeline failed: {e}", exc_info=True)
        notifier.post_error(app.client, f"Post-market pipeline error: {e}")


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

    # Pre-market broker sync: 07:15 AM — before the morning screen
    # Catches overnight fund additions and corporate actions
    scheduler.add_job(
        _run_broker_sync,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=15),
        id="pre_market_sync",
        name="Pre-market broker sync",
    )

    # Pre-market screen: 07:30 AM — Nifty assessment + full swing setup screen
    scheduler.add_job(
        _run_pre_market,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=30),
        id="pre_market",
        name="Pre-market pipeline",
    )

    # Hourly swing monitor: 09:15 through 15:15, Mon–Fri
    # Swing trades don't need 15-minute polling — hourly is ample for
    # limit-order entries and daily-close-based stop/target tracking.
    scheduler.add_job(
        _run_swing_monitor,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute=15),
        id="swing_monitor",
        name="Hourly swing monitor",
    )

    # Post-market EOD review: 15:35 PM
    scheduler.add_job(
        _run_post_market,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=35),
        id="post_market",
        name="Post-market pipeline",
    )

    scheduler.start()
    logger.info(
        "Scheduler started: "
        "sync 07:15 | pre-market 07:30 | swing monitor hourly 09:15–15:15 | post-market 15:35"
    )
    return scheduler


# ── App entrypoint ────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    create_tables()
    logger.info("Database tables ready")

    scheduler = start_scheduler()

    logger.info("Starting Slack bot in Socket Mode")
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()
