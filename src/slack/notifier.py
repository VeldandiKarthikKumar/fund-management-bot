"""
Functions that format and send Slack messages.
All message building lives here — handlers call these, never build blocks inline.
"""
import logging

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _channel() -> str:
    return settings.slack_trading_channel


# ── Message builders ──────────────────────────────────────────────────────

def _suggestion_blocks(suggestion: dict, suggestion_db_id: int) -> list[dict]:
    """
    Interactive Slack message for a swing trade setup.
    Entry is a limit-order zone, not a market order — framed accordingly.
    Buttons: Executed (user confirms they placed the order) or Skip.
    """
    setup     = suggestion["setup"]
    qty       = suggestion["quantity"]
    risk      = suggestion["risk_inr"]
    signals   = ", ".join(s["signal_name"] for s in setup.signals_fired)
    dir_emoji = ":green_circle:" if setup.direction == "BUY" else ":red_circle:"

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📊 Swing Setup: {setup.symbol}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Action:*\n{dir_emoji} {setup.direction}"},
                {"type": "mrkdwn", "text": f"*Limit Entry:*\n₹{setup.entry:,.2f}"},
                {"type": "mrkdwn", "text": f"*Target:*\n₹{setup.target:,.2f}"},
                {"type": "mrkdwn", "text": f"*Stop Loss:*\n₹{setup.stop_loss:,.2f}"},
                {"type": "mrkdwn", "text": f"*R:R Ratio:*\n{setup.risk_reward:.1f}x"},
                {"type": "mrkdwn", "text": f"*Qty:*\n{qty} shares"},
            ],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Risk:*\n₹{risk:,.0f}"},
                {"type": "mrkdwn", "text": f"*Score:*\n{setup.composite_score:.2f}"},
                {"type": "mrkdwn", "text": f"*Signals:*\n{signals}"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        ":pushpin: Place a *limit order* at ₹{entry:,.2f} in your broker app. "
                        "Confirm below once placed."
                    ).format(entry=setup.entry),
                }
            ],
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Executed"},
                    "style": "primary",
                    "action_id": "execute_trade",
                    "value": str(suggestion_db_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⏭️ Skip"},
                    "action_id": "skip_trade",
                    "value": str(suggestion_db_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📈 More Info"},
                    "action_id": "more_info",
                    "value": str(suggestion_db_id),
                },
            ],
        },
    ]


def _exit_alert_blocks(alert: dict) -> list[dict]:
    """Exit alert for an open swing position — user confirms close in broker app."""
    reason_text = "🎯 Target Hit!" if alert["reason"] == "target_hit" else "🛑 Stop Loss Hit!"
    dir_emoji   = ":green_circle:" if alert["action"] == "BUY" else ":red_circle:"

    entry = alert.get("entry_price", 0)
    curr  = alert["current_price"]
    pnl_pct = ((curr - entry) / entry * 100) if entry else 0

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"⚠️ Exit Alert: {alert['symbol']}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{reason_text}*"},
            "fields": [
                {"type": "mrkdwn", "text": f"*Position:*\n{dir_emoji} {alert['action']}"},
                {"type": "mrkdwn", "text": f"*Current Price:*\n₹{curr:,.2f}"},
                {"type": "mrkdwn", "text": f"*Stop:*\n₹{alert['stop']:,.2f}"},
                {"type": "mrkdwn", "text": f"*Target:*\n₹{alert['target']:,.2f}"},
                {"type": "mrkdwn", "text": f"*Unrealised:*\n{pnl_pct:+.1f}%"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": ":pushpin: Close the position in your broker app, then confirm below.",
                }
            ],
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Closed"},
                    "style": "primary",
                    "action_id": "confirm_close",
                    "value": str(alert["position_id"]),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⏳ Holding"},
                    "action_id": "hold_position",
                    "value": str(alert["position_id"]),
                },
            ],
        },
    ]


def _sync_alert_blocks(sync_result) -> list[dict]:
    """
    Notification posted when the broker sync detects positions or fund changes
    that weren't reported through the Slack bot.
    """
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔄 Broker Sync — Changes Detected"},
        }
    ]

    if sync_result.new_positions:
        lines = ["*New positions found in broker (not via bot):*"]
        for p in sync_result.new_positions:
            lines.append(
                f"  • *{p['symbol']}* ×{p['quantity']} shares  "
                f"Avg ₹{p['avg_price']:,.2f}  LTP ₹{p['ltp']:,.2f}\n"
                f"    _Default SL/target set — please review and adjust_"
            )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})

    if sync_result.closed_positions:
        lines = ["*Positions closed in broker (not via bot):*"]
        for p in sync_result.closed_positions:
            pnl_sign = "+" if p["pnl_inr"] >= 0 else ""
            lines.append(
                f"  • *{p['symbol']}*  "
                f"Entry ₹{p['entry_price']:,.2f} → Exit ₹{p['exit_price']:,.2f}  "
                f"P&L ₹{pnl_sign}{p['pnl_inr']:,.0f} ({p['pnl_pct']:+.1f}%)  "
                f"Held {p['held_days']}d"
            )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})

    if sync_result.has_fund_change:
        direction = "added to" if sync_result.fund_change_inr > 0 else "withdrawn from"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":moneybag: *₹{abs(sync_result.fund_change_inr):,.0f} {direction} account*  "
                    f"(balance now ₹{sync_result.fund_balance_inr:,.0f})"
                ),
            },
        })

    return blocks


# ── Send functions ────────────────────────────────────────────────────────

def post_pre_market_brief(client, brief_text: str) -> str:
    """Post morning briefing. Returns message ts."""
    try:
        resp = client.chat_postMessage(
            channel=_channel(),
            text=brief_text,
            mrkdwn=True,
        )
        return resp["ts"]
    except Exception as e:
        logger.error(f"Failed to post pre-market brief: {e}")
        return ""


def post_trade_suggestion(client, suggestion: dict) -> str:
    """Persist suggestion to DB, then post interactive Slack message."""
    from src.db.connection import get_session
    from src.db.repositories.suggestions import SuggestionRepository
    from src.db.repositories.performance import PerformanceRepository

    setup = suggestion["setup"]

    with get_session() as session:
        sugg_repo = SuggestionRepository(session)
        perf_repo = PerformanceRepository(session)

        record = sugg_repo.create(
            symbol=setup.symbol,
            action=setup.direction,
            entry_price=setup.entry,
            target_price=setup.target,
            stop_loss=setup.stop_loss,
            suggested_qty=suggestion["quantity"],
            risk_amount_inr=suggestion["risk_inr"],
            risk_reward=setup.risk_reward,
            signals_fired=setup.signals_fired,
            composite_score=setup.composite_score,
            timeframe=setup.timeframe,
            slack_channel=_channel(),
        )
        db_id = record.id

        blocks = _suggestion_blocks(suggestion, db_id)
        try:
            resp = client.chat_postMessage(
                channel=_channel(),
                blocks=blocks,
                text=f"Swing setup: {setup.symbol} {setup.direction} @ ₹{setup.entry:,.2f}",
            )
            record.slack_ts = resp["ts"]
            ts = resp["ts"]
        except Exception as e:
            logger.error(f"Failed to post trade suggestion: {e}")
            ts = ""

        perf_repo.increment_suggestion_count()

    return ts


def post_suggestions(client, setups: list, context: dict):
    """Post multiple suggestions from the pre-market screen."""
    from src.broker import get_broker
    from src.config import get_settings
    s = get_settings()
    b = get_broker()
    for setup in setups:
        qty = b.compute_quantity(
            capital=s.fund_size_inr,
            entry=setup.entry,
            stop=setup.stop_loss,
            risk_pct=s.max_risk_per_trade_pct / 100,
        )
        post_trade_suggestion(client, {
            "setup":    setup,
            "quantity": qty,
            "risk_inr": abs(setup.entry - setup.stop_loss) * qty,
        })


def post_exit_alert(client, alert: dict) -> str:
    thread_ts = alert.get("slack_thread_ts", "")
    blocks    = _exit_alert_blocks(alert)
    try:
        kwargs = dict(
            channel=_channel(),
            blocks=blocks,
            text=f"Exit alert: {alert['symbol']} — {alert['reason'].replace('_', ' ')}",
        )
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        resp = client.chat_postMessage(**kwargs)
        return resp["ts"]
    except Exception as e:
        logger.error(f"Failed to post exit alert: {e}")
        return ""


def post_sync_alert(client, sync_result) -> str:
    """Post a notification when broker sync finds external changes."""
    blocks = _sync_alert_blocks(sync_result)
    try:
        resp = client.chat_postMessage(
            channel=_channel(),
            blocks=blocks,
            text="Broker sync: changes detected in your account",
        )
        return resp["ts"]
    except Exception as e:
        logger.error(f"Failed to post sync alert: {e}")
        return ""


def post_eod_review(client, review_text: str) -> str:
    try:
        resp = client.chat_postMessage(
            channel=_channel(),
            text=review_text,
            mrkdwn=True,
        )
        return resp["ts"]
    except Exception as e:
        logger.error(f"Failed to post EOD review: {e}")
        return ""


def post_error(client, message: str):
    try:
        client.chat_postMessage(
            channel=_channel(),
            text=f":warning: *Bot error:* {message}",
        )
    except Exception:
        pass
