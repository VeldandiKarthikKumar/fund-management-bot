"""
Handles interactive button callbacks for open positions:
  - confirm_close   → user closed the position in their broker app
  - hold_position   → user is holding despite the alert
"""
import logging

from slack_bolt import App

from src.db.connection import get_session
from src.db.models import ExitReason
from src.db.repositories.positions import PositionRepository
from src.learning.tracker import OutcomeTracker

logger = logging.getLogger(__name__)


def register_position_actions(app: App):

    @app.action("confirm_close")
    def handle_confirm_close(ack, body, say, client):
        ack()
        position_id = int(body["actions"][0]["value"])
        user        = body["user"]["name"]
        channel     = body["channel"]["id"]
        message_ts  = body["message"]["ts"]

        with get_session() as session:
            pos_repo = PositionRepository(session)
            tracker  = OutcomeTracker(session)

            pos = session.get(type(pos_repo.get_open()[0]) if pos_repo.get_open() else None,
                              position_id) if pos_repo.get_open() else None

            # Use last alert price as exit price (user confirmed)
            # In production, prompt user for actual fill price
            from src.broker import get_broker
            broker = get_broker()
            try:
                quotes = broker.get_quote([pos.symbol])
                exit_price = quotes[pos.symbol].last_price
            except Exception:
                exit_price = pos.target  # Fallback

            closed = pos_repo.close(position_id, exit_price, ExitReason.MANUAL)
            tracker.record_close(closed)

            pnl_emoji = ":moneybag:" if closed.pnl_inr > 0 else ":x:"
            client.chat_update(
                channel=channel,
                ts=message_ts,
                text=f"✅ Position closed: {closed.symbol}",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{pnl_emoji} *{closed.symbol} closed* by @{user}\n"
                            f"Exit: ₹{closed.exit_price}  "
                            f"P&L: ₹{closed.pnl_inr:+,.0f} ({closed.pnl_pct:+.1f}%)"
                        ),
                    },
                }],
            )
            say(text=f"Got it! Position recorded as closed. P&L: ₹{closed.pnl_inr:+,.0f}",
                thread_ts=message_ts)

        logger.info(f"Position {position_id} closed by {user}, P&L ₹{closed.pnl_inr:+,.0f}")

    @app.action("hold_position")
    def handle_hold(ack, body, say):
        ack()
        position_id = int(body["actions"][0]["value"])
        user        = body["user"]["name"]
        message_ts  = body["message"]["ts"]

        say(
            text=f"Noted @{user} — holding the position. I'll send another alert if levels are breached.",
            thread_ts=message_ts,
        )
        logger.info(f"User {user} chose to hold position {position_id}")
