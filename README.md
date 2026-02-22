# Fund Management Bot

An automated swing-trading assistant that screens NSE stocks daily, suggests
precise limit-order entries via Slack, and stays in continuous sync with your
broker account — even when you trade or add funds directly in the broker app.

The user executes trades in their broker app and confirms back in Slack (or
doesn't — the hourly sync will detect it either way). The bot tracks every
open position, monitors stops and targets, and continuously learns from
outcomes to improve signal weights over time.

---

## How it works

```
07:15  Pre-market broker sync
         └─ Detect overnight fund additions, corporate actions
07:30  Pre-market screen
         └─ Nifty/VIX assessment + full swing setup screen (Nifty 50 + Midcap 50)
         └─ Top 5 setups posted to Slack with limit-order entry, stop, target
09:15  ─┐
10:15   │  Hourly swing monitor (runs each :15 through 15:15)
11:15   │    ├─ Broker sync (detect external trades + fund changes)
12:15   │    ├─ Check open positions for stop/target hits → exit alerts
13:15   │    └─ Alert if watched setup enters ±1.5% of entry zone
14:15   │
15:15  ─┘
15:35  Post-market EOD review
         └─ Reconcile closed positions, calculate P&L
         └─ Trigger learning module (signal weight calibration)
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Daily Pipelines                           │
│  Pre-market (07:30)  │  Hourly monitor (09:15–15:15)  │  EOD   │
│  Screen + brief      │  Broker sync + entry/exit alerts │ Review │
└─────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────┐
│                  Broker Sync  (src/broker/sync.py)               │
│  Positions in broker not in DB  → create (is_externally_created) │
│  Positions in DB gone from broker → close + record P&L           │
│  Fund balance change > ₹500     → post Slack alert               │
└─────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────┐
│                     Slack Bolt App (two-way)                     │
│  Setup  → [✅ Executed] [⏭️ Skip] [📈 More Info]                  │
│  Exit alert → [✅ Closed] [⏳ Holding]                            │
│  /fundbot status | positions | sync | run | stats | help         │
└─────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────┐
│             Signal Engine  (4 independent signals)               │
│  EMA Crossover  │  RSI Divergence  │  S/R Breakout  │  Volume   │
│  All signals must agree on direction (no conflicting signals)    │
│  Weighted composite score → ranked setups, top 5 posted          │
└─────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────┐
│                          PostgreSQL                              │
│  trade_suggestions  │  positions  │  signal_performance          │
│  daily_journal (fund_balance, fund_added, last_sync_at)          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Slack | Slack Bolt (interactive messages, Socket Mode) |
| Broker | Angel One SmartAPI / Zerodha Kite Connect (pluggable via `BrokerBase`) |
| Signals | pandas-ta (EMA, RSI, ATR) |
| Database | PostgreSQL via SQLAlchemy |
| Scheduler | APScheduler (embedded in the Slack bot process) |
| Containers | Docker + Docker Compose |

---

## Project Structure

```
src/
├── config.py                    # All settings via env vars
├── broker/
│   ├── base.py                  # Abstract broker interface
│   ├── angel_one.py             # Angel One SmartAPI adapter
│   ├── zerodha.py               # Zerodha Kite Connect adapter
│   ├── sync.py                  # Broker↔DB reconciliation (positions + funds)
│   └── __init__.py              # get_broker() factory
├── analysis/
│   ├── signals/
│   │   ├── base.py              # SignalResult + BaseSignal
│   │   ├── ema_crossover.py     # 20/50 EMA crossover (daily)
│   │   ├── rsi.py               # RSI divergence (daily)
│   │   ├── support_resistance.py # S/R breakout/breakdown
│   │   └── volume.py            # Volume breakout
│   └── screener.py              # Runs all signals, computes composite scores
├── pipelines/
│   ├── pre_market.py            # 07:30 AM — watchlist + morning brief
│   ├── intraday.py              # Hourly swing monitor — entry zones + exit alerts
│   └── post_market.py           # 15:35 PM — EOD P&L + trigger learning
├── slack/
│   ├── app.py                   # Slack Bolt app + APScheduler
│   ├── notifier.py              # Message formatting and sending
│   └── handlers/
│       ├── suggestions.py       # Executed / Skip / More Info actions
│       ├── positions.py         # Close / Hold actions
│       └── commands.py          # /fundbot slash commands
├── learning/
│   ├── tracker.py               # Records outcomes per signal after trade closes
│   └── calibrator.py            # Adjusts signal weights weekly
└── db/
    ├── models.py                # SQLAlchemy ORM models
    ├── connection.py            # Session management
    └── repositories/            # Data access layer
        ├── positions.py
        ├── suggestions.py
        └── performance.py
```

---

## Quick Start (Local)

```bash
# 1. Clone and configure
git clone <repo-url>
cd fund-management-bot
cp .env.example .env
# Fill in .env — see Environment Variables section below

# 2. Start everything with Docker
docker-compose up

# Or run bot only (assumes Postgres is already running)
docker-compose up db -d
pip install -r requirements-dev.txt
python -m src.slack.app
```

---

## Environment Variables

```env
# Broker (set BROKER=angel_one or BROKER=zerodha)
BROKER=angel_one

# Angel One
ANGEL_ONE_API_KEY=
ANGEL_ONE_CLIENT_ID=
ANGEL_ONE_PASSWORD=
ANGEL_ONE_TOTP_SECRET=

# Zerodha (if using Zerodha)
ZERODHA_API_KEY=
ZERODHA_API_SECRET=
ZERODHA_ACCESS_TOKEN=

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=
SLACK_TRADING_CHANNEL=#fund-bot

# Fund settings
FUND_SIZE_INR=500000
MAX_RISK_PER_TRADE_PCT=1.5
MAX_OPEN_POSITIONS=5

# Database
DATABASE_URL=postgresql://fundbot:fundbot_local@db:5432/fundbot
```

---

## Slack Setup

1. Create a Slack app at https://api.slack.com/apps
2. Enable **Socket Mode** and generate an App-Level token (`xapp-...`)
3. Add Bot token scopes: `chat:write`, `commands`, `channels:read`
4. Create slash command `/fundbot`
5. Enable **Interactivity** (required for button callbacks)
6. Install the app to your workspace

---

## Slash Commands

| Command | Description |
|---|---|
| `/fundbot status` | Today's market, P&L, fund balance, last sync time |
| `/fundbot positions` | Open swing positions with live prices and unrealised P&L |
| `/fundbot sync` | Manually sync positions and fund balance from broker now |
| `/fundbot run` | Manually trigger the swing monitor screen |
| `/fundbot stats` | Signal win-rates, avg hold days, and performance weights |
| `/fundbot help` | Command reference |

---

## Broker Sync

The bot reconciles its DB state against the actual broker account automatically.
You don't need to inform the bot every time you trade.

| Scenario | What the bot does |
|---|---|
| You bought a stock directly in the broker app | Detects it, creates a position record with a default 6% SL / 10% target |
| You sold a stock directly in the broker app | Detects it, closes the position, records P&L |
| You added funds to the broker account | Detects the balance change, posts a Slack notification |

Sync runs at: **07:15**, every **hourly monitor tick**, and **15:35**. Also available on-demand via `/fundbot sync`.

---

## Swing Trading Signals

All signals use **daily timeframe data** (180 days of history). A setup is only
surfaced if all fired signals agree on direction.

| Signal | Logic |
|---|---|
| EMA Crossover | 20-day EMA crosses above/below 50-day EMA |
| RSI Divergence | Price and RSI diverging (bullish or bearish) on daily chart |
| S/R Breakout | Breakout above resistance or below support with volume confirmation |
| Volume Breakout | Exceptional volume (≥2× avg) with strong directional candle |

Entry zone: **±1.5%** of the suggested limit price (wide enough for a limit order to fill without requiring exact tick precision).

---

## Learning Loop

- Every closed trade updates `SignalPerformance` for each contributing signal
- The `Calibrator` runs weekly (Saturday) and adjusts weights:
  - Win rate > 60% and avg P&L > 1.5% → weight increases (cap 2.0×)
  - Win rate < 35% or avg P&L < −1% → weight decreases (floor 0.1)
- Weights feed back into the screener's composite score

---

## Risk Management

- Default: **1.5%** of fund at risk per trade
- Position size: `risk_amount / |entry − stop_loss|`
- Maximum **5** concurrent open positions
- Minimum **2:1 R:R** required before a suggestion is posted
- Stop loss and target are based on swing structure (ATR, pivots, S/R levels)

---

## Adding New Signals

1. Create `src/analysis/signals/my_signal.py` extending `BaseSignal`
2. Implement `analyze(df, symbol) -> Optional[SignalResult]`
3. Add to `_build_signals()` in `screener.py`
4. The learning module tracks its performance automatically from day one
