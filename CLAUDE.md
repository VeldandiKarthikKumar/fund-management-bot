# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install dependencies:**
```bash
pip install -r requirements.txt        # production
pip install -r requirements-dev.txt    # dev tools (pytest, ruff, mypy, black)
```

**Run locally:**
```bash
docker-compose up                                        # full stack (Postgres + bot)
docker-compose up db -d && python -m src.slack.app       # DB in Docker, bot locally
```

**Run a pipeline manually:**
```bash
python -m src.pipelines.pre_market
python -m src.pipelines.intraday
python -m src.learning.calibrator
```

**Lint / type-check / test:**
```bash
ruff check src/ tests/
mypy src/ --ignore-missing-imports
pytest tests/ -v --cov=src
pytest tests/test_signals.py::test_ema_crossover -v   # single test
```

**Database migrations:**
```bash
alembic upgrade head
alembic revision --autogenerate -m "description"
```

## Architecture

The bot runs as a single long-lived process: a **Slack Bolt app** (`src/slack/app.py`) with **APScheduler** embedded. All pipelines and broker syncs are triggered as cron jobs inside that process—there is no separate scheduler service.

### Daily schedule (IST, Mon–Fri)
| Time | Job |
|------|-----|
| 07:15 | Broker sync (fund additions, corporate actions) |
| 07:30 | Pre-market screen → top 5 setups to Slack |
| 09:15–15:15 | Hourly swing monitor (broker sync + stop/target + entry alerts) |
| 15:35 | Post-market EOD review + learning trigger |
| Saturday | Signal weight calibration (`src/learning/calibrator.py`) |

### Key design decisions
- **Broker abstraction** (`src/broker/base.py`): `BrokerBase` is an abstract class. `get_broker()` in `src/broker/__init__.py` returns an `AngelOneBroker` or `ZerodhaBroker` depending on the `BROKER` env var.
- **Signal consensus rule**: All active signals must agree on direction. If any signal fires in the opposite direction the setup is discarded entirely.
- **Composite score** = `Σ(signal_strength × weight) / Σ(weights)`. Weights live in `src/config.py` and are updated by the calibrator.
- **Broker sync** (`src/broker/sync.py`) does full bidirectional reconciliation: positions opened externally get an `is_externally_created=True` `Position` record; positions gone from the broker get closed with P&L recorded.
- **Position sizing**: fixed-risk model — risk amount = `FUND_SIZE_INR × MAX_RISK_PER_TRADE_PCT / 100`; quantity = `risk_amount / (entry − stop)`.

### Module map
| Path | Responsibility |
|------|---------------|
| `src/config.py` | All settings (Pydantic `BaseSettings`); signal weights, risk params, watchlist |
| `src/broker/` | `BrokerBase` interface + Angel One / Zerodha adapters + sync |
| `src/analysis/signals/` | Four independent signals: `ema_crossover`, `rsi`, `support_resistance`, `volume` |
| `src/analysis/screener.py` | Runs signals on all watchlist symbols, filters by R:R ≥ 2:1, ranks by score |
| `src/pipelines/` | `pre_market`, `intraday`, `post_market` workflows |
| `src/slack/app.py` | Slack Bolt + APScheduler entry point |
| `src/slack/handlers/` | `commands.py` (`/fundbot`), `suggestions.py` (setup buttons), `positions.py` (exit buttons) |
| `src/slack/notifier.py` | Message formatting and Slack delivery |
| `src/db/models.py` | SQLAlchemy ORM: `TradeSuggestion`, `Position`, `SignalPerformance`, `DailyJournal` |
| `src/db/repositories/` | Data access layer (positions, suggestions, performance) |
| `src/learning/tracker.py` | Records per-signal outcomes when a position closes |
| `src/learning/calibrator.py` | Weekly weight adjustment (needs ≥10 trades; ±0.1 step, bounds 0.1–2.0) |

### Infrastructure
- **AWS**: ECS Fargate (single task), ECR (images), RDS PostgreSQL, S3 (token storage), Secrets Manager (all credentials), CloudWatch (logs).
- **Terraform** in `terraform/`; `terraform/ecs.tf` defines the `fundbot-prod-slack-bot` service.
- **CI/CD**: `.github/workflows/test.yml` (lint → type-check → pytest on every PR/push) and `.github/workflows/deploy.yml` (build → push ECR → force-deploy ECS on `main` push).
