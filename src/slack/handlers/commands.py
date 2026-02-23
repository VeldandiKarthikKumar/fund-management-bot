"""
Slash command handlers.

/fundbot status      → today's journal + open positions summary
/fundbot positions   → open positions with live prices and unrealised P&L
/fundbot sync        → manually trigger broker sync (positions + funds)
/fundbot run         → manually trigger swing monitor screen
/fundbot stats       → signal performance stats
/fundbot help        → command reference
"""

import logging

from slack_bolt import App

from src.db.connection import get_session
from src.db.repositories.performance import PerformanceRepository
from src.db.repositories.positions import PositionRepository

logger = logging.getLogger(__name__)


def register_commands(app: App):

    @app.command("/fundbot")
    def handle_fundbot(ack, respond, command):
        ack()
        subcommand = (command.get("text") or "help").strip().lower()

        if subcommand == "status":
            _status(respond)
        elif subcommand == "positions":
            _positions(respond)
        elif subcommand == "sync":
            _sync(respond)
        elif subcommand == "run":
            _manual_run(respond)
        elif subcommand == "stats":
            _stats(respond)
        else:
            _help(respond)

    def _status(respond):
        with get_session() as session:
            perf_repo = PerformanceRepository(session)
            pos_repo = PositionRepository(session)
            journal = perf_repo.get_or_create_today()
            portfolio = pos_repo.get_portfolio_summary()

            sync_ts = (
                journal.last_sync_at.strftime("%H:%M")
                if journal.last_sync_at
                else "not yet"
            )
            fund_line = ""
            if journal.fund_added_inr:
                fund_line = f"\nFunds added today: ₹{journal.fund_added_inr:,.0f}"
            if journal.fund_balance_inr:
                fund_line += f"  |  Available: ₹{journal.fund_balance_inr:,.0f}"

            respond(
                text=(
                    f"*Today's status*\n"
                    f"Market: {journal.nifty_trend or 'pending'}  |  "
                    f"VIX: {journal.vix_level or 'N/A'}  |  "
                    f"Last sync: {sync_ts}\n"
                    f"Signals sent: {journal.suggestions_sent}  |  "
                    f"Executed: {journal.suggestions_executed}  |  "
                    f"Skipped: {journal.suggestions_skipped}\n"
                    f"Open positions: {portfolio['count']}/{5}  |  "
                    f"Invested: ₹{portfolio['total_invested_inr']:,.0f}\n"
                    f"Today's P&L: ₹{journal.total_pnl_inr or 0:+,.0f}"
                    f"{fund_line}"
                ),
                response_type="in_channel",
            )

    def _positions(respond):
        with get_session() as session:
            pos_repo = PositionRepository(session)

            try:
                from src.broker import get_broker

                broker = get_broker()
                symbols = [p.symbol for p in pos_repo.get_open()]
                quotes = broker.get_quote(symbols) if symbols else {}
            except Exception:
                quotes = {}

            portfolio = pos_repo.get_portfolio_summary()
            if not portfolio["positions"]:
                respond(text="No open positions.")
                return

            lines = ["*Open swing positions:*"]
            for p in portfolio["positions"]:
                quote = quotes.get(p["symbol"])
                curr_price = quote.last_price if quote else None
                ext_tag = " _(external)_" if p.get("is_externally_created") else ""
                price_str = f"₹{curr_price:,.2f}" if curr_price else "?"
                pnl_str = ""
                if curr_price:
                    unreal = (curr_price - p["entry"]) * p["qty"]
                    pnl_str = f"  Unrealised: ₹{unreal:+,.0f}"
                lines.append(
                    f"  • *{p['symbol']}* {p['action']}{ext_tag}  "
                    f"Entry ₹{p['entry']:,.2f}  Now {price_str}  "
                    f"SL ₹{p['stop']:,.2f}  Target ₹{p['target']:,.2f}"
                    f"{pnl_str}"
                )
            respond(text="\n".join(lines), response_type="in_channel")

    def _sync(respond):
        """Manually trigger a broker sync and report what changed."""
        respond(
            text=":arrows_counterclockwise: Syncing with broker…",
            response_type="ephemeral",
        )
        try:
            from src.broker import get_broker
            from src.broker.sync import run_sync
            from src.db.repositories.performance import PerformanceRepository
            from src.slack import notifier
            from src.slack.app import app as slack_app

            broker = get_broker()
            with get_session() as session:
                perf_repo = PerformanceRepository(session)
                journal = perf_repo.get_or_create_today()
                last_balance = journal.fund_balance_inr or 0.0
                result = run_sync(broker, session, last_known_balance=last_balance)
                if result.fund_balance_inr:
                    journal.fund_balance_inr = result.fund_balance_inr
                    journal.fund_added_inr = (journal.fund_added_inr or 0.0) + max(
                        0.0, result.fund_change_inr
                    )

            if result.has_position_changes or result.has_fund_change:
                notifier.post_sync_alert(slack_app.client, result)
                summary = (
                    f":white_check_mark: Sync complete — "
                    f"{len(result.new_positions)} new position(s), "
                    f"{len(result.closed_positions)} closed. "
                    f"Balance: ₹{result.fund_balance_inr:,.0f}"
                )
            else:
                summary = (
                    ":white_check_mark: Sync complete — everything in sync, no changes."
                )

            respond(text=summary, response_type="ephemeral")
        except Exception as e:
            respond(text=f":x: Sync failed: {e}", response_type="ephemeral")

    def _manual_run(respond):
        respond(
            text=":hourglass: Running swing monitor now…", response_type="ephemeral"
        )
        try:
            from src.pipelines.intraday import run
            from src.slack import notifier
            from src.slack.app import app as slack_app

            result = run()
            sync = result.get("sync")
            if sync and (sync.has_position_changes or sync.has_fund_change):
                notifier.post_sync_alert(slack_app.client, sync)
            if result.get("new_suggestions"):
                for s in result["new_suggestions"]:
                    notifier.post_trade_suggestion(slack_app.client, s)
                respond(
                    text=f":white_check_mark: Found {len(result['new_suggestions'])} new swing setup(s).",
                    response_type="ephemeral",
                )
            else:
                respond(
                    text=":zzz: No new setups at this time.", response_type="ephemeral"
                )
        except Exception as e:
            respond(text=f":x: Error: {e}", response_type="ephemeral")

    def _stats(respond):
        with get_session() as session:
            perf_repo = PerformanceRepository(session)
            stats = perf_repo.get_all_signal_stats()

            if not stats:
                respond(text="No signal performance data yet.")
                return

            lines = ["*Signal performance stats:*"]
            for s in sorted(stats, key=lambda x: x.win_rate, reverse=True):
                lines.append(
                    f"  • *{s.signal_name}* ({s.timeframe})  "
                    f"Win rate: {s.win_rate*100:.0f}%  "
                    f"Avg P&L: {s.avg_pnl_pct:+.1f}%  "
                    f"Avg hold: {s.avg_held_days:.0f}d  "
                    f"Trades: {s.executed_signals}  "
                    f"Weight: {s.signal_weight:.2f}"
                )
            respond(text="\n".join(lines), response_type="in_channel")

    def _help(respond):
        respond(
            text=(
                "*FundBot commands:*\n"
                "  `/fundbot status`    — Today's journal, P&L, fund balance\n"
                "  `/fundbot positions` — Open swing positions with live prices\n"
                "  `/fundbot sync`      — Sync positions and funds from broker now\n"
                "  `/fundbot run`       — Manually trigger swing monitor screen\n"
                "  `/fundbot stats`     — Signal win-rate and performance stats\n"
                "  `/fundbot help`      — This message"
            ),
            response_type="ephemeral",
        )
