"""
Fetches the live Nifty 200 constituent list from NSE archives.
Used by pipelines to build a fresh stock universe each trading day.

Result is cached in-process (Nifty 200 rebalances quarterly, so one
fetch per process startup is sufficient).
"""

import io
import logging
from functools import lru_cache

import pandas as pd
import requests

from src.config import get_settings

logger = logging.getLogger(__name__)

_NSE_NIFTY200_CSV = (
    "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
)


@lru_cache(maxsize=1)
def get_nifty200_symbols() -> list[str]:
    """
    Return current Nifty 200 constituents as a list of NSE symbols.
    Falls back to settings.watchlist if the NSE fetch fails.
    """
    try:
        resp = requests.get(
            _NSE_NIFTY200_CSV,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        symbols = df["Symbol"].str.strip().tolist()
        logger.info(f"Loaded {len(symbols)} Nifty 200 constituents from NSE")
        return symbols
    except Exception as e:
        logger.warning(
            f"Failed to fetch Nifty 200 from NSE ({e}); "
            "falling back to config watchlist"
        )
        return get_settings().watchlist
