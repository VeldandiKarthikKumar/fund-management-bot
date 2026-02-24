"""
Centralized configuration via environment variables.
All secrets and settings come through here — never hardcoded elsewhere.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────
    database_url: str  # postgresql://user:pass@host:5432/fundbot

    # ── Broker selector ───────────────────────────────────────────────────
    broker: str = "angel_one"  # "angel_one" | "zerodha"

    # ── Angel One SmartAPI ────────────────────────────────────────────────
    angel_one_api_key: str = ""
    angel_one_client_id: str = ""  # Your Angel One login ID (e.g. A12345)
    angel_one_password: str = ""  # Your Angel One login PIN
    angel_one_totp_secret: str = ""  # Base32 secret from your TOTP authenticator setup
    angel_one_jwt_token: str = ""  # Populated automatically after daily auth

    # ── Zerodha (kept for optional use) ───────────────────────────────────
    zerodha_api_key: str = ""
    zerodha_api_secret: str = ""
    zerodha_redirect_url: str = ""
    zerodha_access_token: str = ""  # Refreshed daily via OAuth

    # ── Slack ─────────────────────────────────────────────────────────────
    slack_bot_token: str  # xoxb-...
    slack_app_token: str  # xapp-... (for Socket Mode)
    slack_signing_secret: str
    slack_trading_channel: str  # #trading-signals

    # ── AWS ───────────────────────────────────────────────────────────────
    aws_region: str = "ap-south-1"
    s3_bucket_name: str = "fundbot-tokens"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    # ── Market timing (24h IST) ───────────────────────────────────────────
    pre_market_start: str = "07:30"  # News + technical prep
    market_open: str = "09:15"
    market_close: str = "15:30"
    post_market_start: str = "15:35"  # Review + learning

    # ── Risk management defaults ──────────────────────────────────────────
    fund_size_inr: float = 500_000.0  # Total fund size
    max_risk_per_trade_pct: float = 1.5  # % of fund at risk per trade
    max_open_positions: int = 5
    min_risk_reward: float = 2.0  # Minimum R:R to take a trade

    # ── Stock universe ────────────────────────────────────────────────────
    # Nifty 50 + Midcap 50 — editable without code changes
    watchlist: list[str] = [
        # Nifty 50 (large caps)
        "RELIANCE",
        "TCS",
        "HDFCBANK",
        "INFY",
        "ICICIBANK",
        "HINDUNILVR",
        "ITC",
        "SBIN",
        "BHARTIARTL",
        "KOTAKBANK",
        "LT",
        "AXISBANK",
        "ASIANPAINT",
        "MARUTI",
        "TITAN",
        "SUNPHARMA",
        "BAJFINANCE",
        "WIPRO",
        "ULTRACEMCO",
        "NTPC",
        "POWERGRID",
        "ONGC",
        "JSWSTEEL",
        "TATAMOTORS",
        "TECHM",
        "HCLTECH",
        "BAJAJFINSV",
        "GRASIM",
        "ADANIENT",
        "ADANIPORTS",
        "COALINDIA",
        "BRITANNIA",
        "DIVISLAB",
        "DRREDDY",
        "NESTLEIND",
        "CIPLA",
        "EICHERMOT",
        "TATACONSUM",
        "BPCL",
        "SHRIRAMFIN",
        "APOLLOHOSP",
        "HEROMOTOCO",
        "TRENT",
        "INDUSINDBK",
        "HINDALCO",
        "BAJAJ-AUTO",
        "TATASTEEL",
        "M&M",
        "SBILIFE",
        "HDFCLIFE",
        # Nifty Midcap 50 (selective)
        "MUTHOOTFIN",
        "PIIND",
        "PERSISTENT",
        "COFORGE",
        "LTIM",
        "ABCAPITAL",
        "FEDERALBNK",
        "IDFCFIRSTB",
        "BANDHANBNK",
        "PNB",
        "CANBK",
        "UNIONBANK",
        "MARICO",
        "GODREJCP",
        "DABUR",
        "BERGEPAINT",
        "VOLTAS",
        "HAVELLS",
        "POLYCAB",
        "DIXON",
    ]

    # ── Signal weights (tuned by learning module over time) ───────────────
    signal_weights: dict = {
        "ema_crossover": 1.0,
        "rsi_divergence": 1.0,
        "support_resistance": 1.0,
        "volume_breakout": 1.0,
    }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
