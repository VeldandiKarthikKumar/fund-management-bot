"""
Zerodha Kite Connect adapter.
"""

import logging
from datetime import datetime

import pandas as pd
from kiteconnect import KiteConnect

from src.broker.base import BrokerBase, Instrument, Quote
from src.config import get_settings

logger = logging.getLogger(__name__)


class ZerodhaAdapter(BrokerBase):
    def __init__(self):
        settings = get_settings()
        self._kite = KiteConnect(api_key=settings.zerodha_api_key)
        self._instruments_cache: dict[str, Instrument] = {}

        if settings.zerodha_access_token:
            self.set_access_token(settings.zerodha_access_token)

    # ── Auth ──────────────────────────────────────────────────────────────

    def authenticate(self, request_token: str) -> str:
        settings = get_settings()
        data = self._kite.generate_session(
            request_token, api_secret=settings.zerodha_api_secret
        )
        access_token = data["access_token"]
        self.set_access_token(access_token)
        logger.info("Zerodha session created successfully.")
        return access_token

    def set_access_token(self, token: str) -> None:
        self._kite.set_access_token(token)

    def get_login_url(self) -> str:
        return self._kite.login_url()

    # ── Market data ───────────────────────────────────────────────────────

    def get_historical_data(
        self,
        symbol: str,
        interval: str,
        from_date: datetime,
        to_date: datetime,
        exchange: str = "NSE",
    ) -> pd.DataFrame:
        instrument = self.get_instrument(symbol, exchange)
        try:
            records = self._kite.historical_data(
                instrument_token=instrument.token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
            )
        except Exception as e:
            logger.error(f"Failed to fetch historical data for {symbol}: {e}")
            raise

        df = pd.DataFrame(records)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df[["open", "high", "low", "close", "volume"]]
        return df

    def get_quote(self, symbols: list[str], exchange: str = "NSE") -> dict[str, Quote]:
        keys = [f"{exchange}:{s}" for s in symbols]
        raw = self._kite.quote(keys)
        result = {}
        for symbol in symbols:
            key = f"{exchange}:{symbol}"
            if key not in raw:
                continue
            d = raw[key]
            ohlc = d.get("ohlc", {})
            result[symbol] = Quote(
                symbol=symbol,
                last_price=d["last_price"],
                open=ohlc.get("open", 0),
                high=ohlc.get("high", 0),
                low=ohlc.get("low", 0),
                close=ohlc.get("close", 0),
                volume=d.get("volume", 0),
                timestamp=datetime.now(),
            )
        return result

    def get_instrument(self, symbol: str, exchange: str = "NSE") -> Instrument:
        if symbol in self._instruments_cache:
            return self._instruments_cache[symbol]

        instruments = self._kite.instruments(exchange)
        for inst in instruments:
            if inst["tradingsymbol"] == symbol and inst["exchange"] == exchange:
                result = Instrument(
                    symbol=symbol,
                    token=inst["instrument_token"],
                    exchange=exchange,
                    lot_size=inst.get("lot_size", 1),
                    tick_size=inst.get("tick_size", 0.05),
                )
                self._instruments_cache[symbol] = result
                return result

        raise ValueError(f"Instrument not found: {exchange}:{symbol}")

    # ── Portfolio ─────────────────────────────────────────────────────────

    def is_market_open(self) -> bool:
        try:
            quote = self._kite.quote(["NSE:NIFTY 50"])
            return bool(quote)
        except Exception:
            return False

    def get_holdings(self) -> list[dict]:
        return self._kite.holdings()

    def get_positions(self) -> list[dict]:
        return self._kite.positions().get("net", [])
