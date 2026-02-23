"""
Handles interactive button callbacks for trade suggestions:
  - execute_trade   → user clicked "Execute" on a suggestion
  - skip_trade      → user clicked "Skip"
  - more_info       → user wants signal details
"""

import logging

from slack_bolt import App

from src.db.connection import get_session
from src.db.repositories.positions import PositionRepository
from src.db.repositories.suggestions import SuggestionRepository
from src.db.repositories.performance import PerformanceRepository

logger = logging.getLogger(__name__)


def register_suggestion_actions(app: App):

    @app.action("execute_trade")
    def handle_execute(ack, body, say, client):
        ack()
        suggestion_id = int(body["actions"][0]["value"])
        user = body["user"]["name"]
        channel = body["channel"]["id"]
        message_ts = body["message"]["ts"]

        with get_session() as session:
            sugg_repo = SuggestionRepository(session)
            pos_repo = PositionRepository(session)
            perf_repo = PerformanceRepository(session)

            s = sugg_repo.mark_executed(suggestion_id)
            if not s:
                say(text=":x: Suggestion not found.", thread_ts=message_ts)
                return

            # Create position record
            pos = pos_repo.create(
                suggestion_id=s.id,
                symbol=s.symbol,
                action=s.action,
                entry_price=s.entry_price,
                quantity=s.suggested_qty,
                target=s.target_price,
                stop=s.stop_loss,
                slack_thread_ts=message_ts,
            )

            perf_repo.increment_suggestion_count(executed=True)

            # Update the original message to reflect execution
            client.chat_update(
                channel=channel,
                ts=message_ts,
                text=f"✅ *{s.symbol} {s.action}* — Executed by @{user}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"✅ *{s.symbol} {s.action}* executed by @{user}\n"
                                f"Entry: ₹{s.entry_price}  Target: ₹{s.target_price}  "
                                f"SL: ₹{s.stop_loss}  Qty: {s.suggested_qty}"
                            ),
                        },
                    }
                ],
            )

            say(
                text=(
                    f"Position #{pos.id} opened: *{s.symbol} {s.action}* "
                    f"@ ₹{s.entry_price}  |  SL: ₹{s.stop_loss}  Target: ₹{s.target_price}\n"
                    f"I'll alert you when price hits target or stop."
                ),
                thread_ts=message_ts,
                mrkdwn=True,
            )

        logger.info(f"Trade executed: {s.symbol} {s.action} by {user}")

    @app.action("skip_trade")
    def handle_skip(ack, body, say, client):
        ack()
        suggestion_id = int(body["actions"][0]["value"])
        user = body["user"]["name"]
        channel = body["channel"]["id"]
        message_ts = body["message"]["ts"]

        with get_session() as session:
            sugg_repo = SuggestionRepository(session)
            perf_repo = PerformanceRepository(session)

            s = sugg_repo.mark_skipped(suggestion_id)
            perf_repo.increment_suggestion_count(skipped=True)

            client.chat_update(
                channel=channel,
                ts=message_ts,
                text=f"⏭️ *{s.symbol}* — Skipped by @{user}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"⏭️ *{s.symbol} {s.action}* skipped by @{user}",
                        },
                    }
                ],
            )

        logger.info(f"Trade skipped: {s.symbol} by {user}")

    @app.action("more_info")
    def handle_more_info(ack, body, say):
        ack()
        suggestion_id = int(body["actions"][0]["value"])
        message_ts = body["message"]["ts"]

        with get_session() as session:
            sugg_repo = SuggestionRepository(session)
            s = sugg_repo.get_by_id(suggestion_id)
            if not s:
                return

            signals_text = "\n".join(
                f"  • *{sig['signal_name']}*: strength {sig['strength']:.2f}, "
                f"R:R {sig.get('risk_reward', '?')}x"
                for sig in (s.signals_fired or [])
            )

            say(
                text=f"Signal details for {s.symbol}:\n{signals_text}",
                thread_ts=message_ts,
                mrkdwn=True,
            )
