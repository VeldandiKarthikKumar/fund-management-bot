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
import time
from collections import deque
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
        # Full master list + O(1) index, loaded lazily
        self._master: Optional[list[dict]] = None
        # Index: (exch_seg_upper, symbol_upper) → master entry
        self._master_index: dict[tuple[str, str], dict] = {}
        # Guard: attempt re-auth at most once per adapter instance lifetime
        self._reauth_attempted: bool = False
        # Sliding-window rate limiter for historical data: Angel One allows
        # max 3 getCandleData calls/second, 180/minute, 5000/hour.
        self._hist_call_times: deque = deque()

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

        token_data = data.get("data") or {}
        jwt_token = token_data.get("jwtToken", "")
        if not jwt_token:
            raise RuntimeError(
                f"Angel One generateSession returned no token — "
                f"status={data.get('status')}, message={data.get('message')}"
            )
        # generateSession pre-pends "Bearer " to jwtToken in its return value,
        # but SmartConnect._request() adds "Bearer " again when building the
        # Authorization header. Strip it here so the header is correct.
        if jwt_token.startswith("Bearer "):
            jwt_token = jwt_token[len("Bearer "):]
        self._feed_token = token_data.get("feedToken", "")
        self.set_access_token(jwt_token)
        logger.info("Angel One session created successfully.")
        return jwt_token

    def set_access_token(self, token: str) -> None:
        """Load a previously obtained JWT token into the client."""
        self._jwt_token = token
        self._obj.setAccessToken(token)
        self._obj.setSessionExpiryHook(self._on_session_expired)

    def _on_session_expired(self):
        """Callback triggered by SmartAPI when the token expires — re-auth."""
        logger.warning("Angel One session expired; re-authenticating.")
        self.authenticate()

    def _throttle_historical(self) -> None:
        """Enforce ≤3 getCandleData calls per second (Angel One rate limit)."""
        now = time.monotonic()
        # Drop timestamps older than 1 second
        while self._hist_call_times and now - self._hist_call_times[0] >= 1.0:
            self._hist_call_times.popleft()
        # If at the per-second cap, sleep until the oldest slot expires
        if len(self._hist_call_times) >= 3:
            sleep_for = 1.0 - (now - self._hist_call_times[0]) + 0.02
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            while self._hist_call_times and now - self._hist_call_times[0] >= 1.0:
                self._hist_call_times.popleft()
        self._hist_call_times.append(time.monotonic())

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

        logger.debug(
            f"getCandleData {symbol} token={instrument.token} "
            f"{historic_param['fromdate']} → {historic_param['todate']}"
        )
        self._throttle_historical()
        try:
            response = self._obj.getCandleData(historic_param)
        except Exception as e:
            logger.error(f"Failed to fetch historical data for {symbol}: {e}")
            raise

        if not response.get("status"):
            msg = response.get("message", "unknown error")
            if "Invalid Token" in msg and not self._reauth_attempted:
                self._reauth_attempted = True
                logger.warning("Angel One token invalid; re-authenticating (once).")
                self.authenticate()  # raises on failure — propagates as fetch_error
                self._reauth_attempted = False  # reset so future expiries in same session are handled
                return self.get_historical_data(symbol, interval, from_date, to_date, exchange)
            raise RuntimeError(f"getCandleData failed for {symbol}: {msg}")

        candles = response.get("data") or []
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
                raw = self._obj.ltpData(
                    exchange=exchange,
                    tradingsymbol=symbol,
                    symboltoken=str(instrument.token),
                )
                if not raw.get("status"):
                    raise RuntimeError(raw.get("message", "ltpData failed"))
                d = raw.get("data") or {}
                result[symbol] = Quote(
                    symbol=symbol,
                    last_price=float(d.get("ltp", 0)),
                    open=float(d.get("open", 0)),
                    high=float(d.get("high", 0)),
                    low=float(d.get("low", 0)),
                    close=float(d.get("close", 0)),
                    volume=0,  # ltpData does not return volume
                    timestamp=datetime.now(),
                )
            except Exception as e:
                logger.warning(f"Failed to get quote for {symbol}: {e}")
        return result

    def get_instrument(self, symbol: str, exchange: str = "NSE") -> Instrument:
        cache_key = f"{exchange}:{symbol}"
        if cache_key in self._instruments_cache:
            return self._instruments_cache[cache_key]

        self._load_instrument_master()
        symbol_upper = symbol.upper()
        # Angel One naming conventions:
        #   Standard equity : {SYMBOL}-EQ  (e.g. "RELIANCE-EQ")
        #   T2T/BE segment  : {SYMBOL}-BE  (e.g. "TATAMOTORS-BE")
        #   Indices         : mixed-case bare name (e.g. "Nifty 50", "India VIX")
        # Prefer -EQ (regular equity) over -BE (trade-to-trade), then bare name.
        entry = None
        matched_name = None
        for candidate in [f"{symbol_upper}-EQ", f"{symbol_upper}-BE", symbol_upper]:
            entry = self._master_index.get((exchange.upper(), candidate))
            if entry:
                matched_name = candidate
                break

        if not entry:
            raise ValueError(f"Instrument not found: {exchange}:{symbol}")

        instrument = Instrument(
            symbol=symbol,
            token=int(entry["token"]),
            exchange=exchange,
            lot_size=int(entry.get("lotsize", 1)),
            tick_size=float(entry.get("tick_size", 0.05)),
        )
        logger.debug(
            f"Token resolved: {exchange}:{symbol} → {matched_name} token={instrument.token}"
        )
        self._instruments_cache[cache_key] = instrument
        return instrument

    def warm_instrument_cache(self, symbols: list[str], exchange: str = "NSE") -> None:
        """
        Pre-resolve and cache symbol tokens for all given symbols.
        Logs the full symbol→token map at INFO level so tokens can be audited,
        and warns about any symbols not found in the master.
        Call this once before a screener run to catch bad tokens early.
        """
        self._load_instrument_master()
        token_map: dict[str, int] = {}
        missing: list[str] = []
        for symbol in symbols:
            try:
                instrument = self.get_instrument(symbol, exchange)
                token_map[symbol] = instrument.token
            except ValueError:
                missing.append(symbol)

        logger.info(
            f"Symbol tokens resolved: {len(token_map)}/{len(symbols)} found. "
            + (f"Missing: {missing}" if missing else "All symbols found.")
        )
        if token_map:
            token_list = ", ".join(f"{s}={t}" for s, t in sorted(token_map.items()))
            logger.debug(f"Token map: {token_list}")

    def _load_instrument_master(self) -> None:
        """Fetch and cache the Angel One instrument master JSON, building an O(1) index."""
        if self._master is not None:
            return
        try:
            resp = requests.get(_INSTRUMENT_MASTER_URL, timeout=30)
            resp.raise_for_status()
            self._master = resp.json()
            # Build index: (exch_seg_upper, symbol_upper) → entry
            # For any duplicate keys, prefer the -EQ entry over -BE.
            for entry in self._master:
                key = (
                    entry.get("exch_seg", "").upper(),
                    entry.get("symbol", "").upper(),
                )
                existing = self._master_index.get(key)
                if existing is None or entry.get("symbol", "").upper().endswith("-EQ"):
                    self._master_index[key] = entry
            logger.info(
                f"Loaded {len(self._master)} instruments from Angel One master "
                f"({len(self._master_index)} unique keys indexed)."
            )
        except Exception as e:
            logger.error(f"Failed to load Angel One instrument master: {e}")
            self._master = []

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
