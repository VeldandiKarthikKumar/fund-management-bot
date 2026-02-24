"""
Angel One SmartAPI adapter.
Uses the official `smartapi-python` client library.

Auth flow:
  1. Create SmartConnect with api_key
  2. Call generateSession(client_id, password, totp) once per day
  3. Store the returned jwtToken for all subsequent calls

TOTP is derived from ANGEL_ONE_TOTP_SECRET using pyotp — the same secret
you scan into Google Authenticator / any TOTP app when enabling 2FA on your
Angel One account.
"""

import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import pyotp
import requests

from src.broker.base import BrokerBase, Instrument, Quote
from src.config import get_settings

logger = logging.getLogger(__name__)

# ── Interval mapping ──────────────────────────────────────────────────────────
# BrokerBase uses Zerodha-style interval strings; map to SmartAPI equivalents.
_INTERVAL_MAP = {
    "5minute": "FIVE_MINUTE",
    "15minute": "FIFTEEN_MINUTE",
    "30minute": "THIRTY_MINUTE",
    "60minute": "ONE_HOUR",
    "day": "ONE_DAY",
    "week": "ONE_DAY",  # SmartAPI has no weekly interval; use daily
}

# Angel One publishes a full instrument master at this URL (refreshed nightly).
_INSTRUMENT_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


class AngelOneAdapter(BrokerBase):
    """
    Angel One SmartAPI adapter implementing BrokerBase.
    Pipelines interact only with BrokerBase methods — Angel One internals
    are fully encapsulated here.
    """

    def __init__(self):
        try:
            from SmartApi import SmartConnect
        except ImportError:
            raise ImportError(
                "smartapi-python is required for the Angel One adapter. "
                "Run: pip install smartapi-python"
            )

        settings = get_settings()
        self._client_id = settings.angel_one_client_id
        self._password = settings.angel_one_password
        self._totp_secret = settings.angel_one_totp_secret
        self._api_key = settings.angel_one_api_key

        self._obj = SmartConnect(api_key=self._api_key)
        self._jwt_token: str = ""
        self._feed_token: str = ""

        # In-memory instrument cache: symbol → Instrument
        self._instruments_cache: dict[str, Instrument] = {}
        # Full master list loaded lazily
        self._master: Optional[list[dict]] = None

        # Auto-authenticate if a stored token exists
        if settings.angel_one_jwt_token:
            self.set_access_token(settings.angel_one_jwt_token)

    # ── Auth ──────────────────────────────────────────────────────────────────

    def authenticate(self, request_token: str = "") -> str:
        """
        Generate a new session using client credentials + TOTP.
        `request_token` is unused for Angel One (kept for interface compat).
        Returns the JWT access token.
        """
        totp = pyotp.TOTP(self._totp_secret).now()
        try:
            data = self._obj.generateSession(self._client_id, self._password, totp)
        except Exception as e:
            logger.error(f"Angel One authentication failed: {e}")
            raise

        token_data = data.get("data", {})
        jwt_token = token_data.get("jwtToken", "")
        self._feed_token = token_data.get("feedToken", "")
        self.set_access_token(jwt_token)
        logger.info("Angel One session created successfully.")
        return jwt_token

    def set_access_token(self, token: str) -> None:
        """Load a previously obtained JWT token into the client."""
        self._jwt_token = token
        self._obj.setSessionExpiryHook(self._on_session_expired)

    def _on_session_expired(self):
        """Callback triggered by SmartAPI when the token expires — re-auth."""
        logger.warning("Angel One session expired; re-authenticating.")
        self.authenticate()

    # ── Market data ───────────────────────────────────────────────────────────

    def get_historical_data(
        self,
        symbol: str,
        interval: str,
        from_date: datetime,
        to_date: datetime,
        exchange: str = "NSE",
    ) -> pd.DataFrame:
        instrument = self.get_instrument(symbol, exchange)
        smartapi_interval = _INTERVAL_MAP.get(interval, "ONE_DAY")

        historic_param = {
            "exchange": exchange,
            "symboltoken": str(instrument.token),
            "interval": smartapi_interval,
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M"),
        }

        try:
            response = self._obj.getCandleData(historic_param)
        except Exception as e:
            logger.error(f"Failed to fetch historical data for {symbol}: {e}")
            raise

        candles = response.get("data", [])
        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            candles, columns=["date", "open", "high", "low", "close", "volume"]
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df

    def get_quote(self, symbols: list[str], exchange: str = "NSE") -> dict[str, Quote]:
        result = {}
        for symbol in symbols:
            try:
                instrument = self.get_instrument(symbol, exchange)
                raw = self._obj.getLTP(
                    exchange=exchange,
                    tradingsymbol=symbol,
                    symboltoken=str(instrument.token),
                )
                d = raw.get("data", {})
                result[symbol] = Quote(
                    symbol=symbol,
                    last_price=float(d.get("ltp", 0)),
                    open=float(d.get("open", 0)),
                    high=float(d.get("high", 0)),
                    low=float(d.get("low", 0)),
                    close=float(d.get("close", 0)),
                    volume=0,  # getLTP does not return volume; fetch separately if needed
                    timestamp=datetime.now(),
                )
            except Exception as e:
                logger.warning(f"Failed to get quote for {symbol}: {e}")
        return result

    def get_instrument(self, symbol: str, exchange: str = "NSE") -> Instrument:
        cache_key = f"{exchange}:{symbol}"
        if cache_key in self._instruments_cache:
            return self._instruments_cache[cache_key]

        master = self._load_instrument_master()
        # Angel One stores NSE equities as "{SYMBOL}-EQ"; also try exact match
        # for BSE or any exchange that uses the bare symbol.
        candidates = (f"{symbol}-EQ", symbol)
        for entry in master:
            if entry.get("exch_seg") == exchange and entry.get("symbol") in candidates:
                instrument = Instrument(
                    symbol=symbol,
                    token=int(entry["token"]),
                    exchange=exchange,
                    lot_size=int(entry.get("lotsize", 1)),
                    tick_size=float(entry.get("tick_size", 0.05)),
                )
                self._instruments_cache[cache_key] = instrument
                return instrument

        raise ValueError(f"Instrument not found: {exchange}:{symbol}")

    def _load_instrument_master(self) -> list[dict]:
        """Fetch and cache the Angel One instrument master JSON."""
        if self._master is not None:
            return self._master
        try:
            resp = requests.get(_INSTRUMENT_MASTER_URL, timeout=30)
            resp.raise_for_status()
            self._master = resp.json()
            logger.info(
                f"Loaded {len(self._master)} instruments from Angel One master."
            )
        except Exception as e:
            logger.error(f"Failed to load Angel One instrument master: {e}")
            self._master = []
        return self._master

    # ── Portfolio ─────────────────────────────────────────────────────────────

    def is_market_open(self) -> bool:
        """Use time-based check (IST 09:15–15:30) — avoids an extra API call."""
        import pytz

        now = datetime.now(pytz.timezone("Asia/Kolkata"))
        if now.weekday() >= 5:  # Saturday / Sunday
            return False
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close

    def get_holdings(self) -> list[dict]:
        try:
            resp = self._obj.holding()
            return resp.get("data", []) or []
        except Exception as e:
            logger.error(f"Failed to fetch holdings: {e}")
            return []

    def get_positions(self) -> list[dict]:
        try:
            resp = self._obj.position()
            return resp.get("data", []) or []
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []
