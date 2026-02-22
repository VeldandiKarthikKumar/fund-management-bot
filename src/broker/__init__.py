"""
Broker factory.
Returns the configured broker adapter as a singleton.

Set BROKER=angel_one (default) or BROKER=zerodha in your .env.
All pipelines and handlers import get_broker() from here — never from
the individual adapter modules — so swapping brokers needs only a one-line
env-var change.
"""
from functools import lru_cache

from src.broker.base import BrokerBase


@lru_cache(maxsize=1)
def get_broker() -> BrokerBase:
    from src.config import get_settings
    settings = get_settings()
    broker = settings.broker.lower()

    if broker == "zerodha":
        from src.broker.zerodha import ZerodhaAdapter
        return ZerodhaAdapter()

    if broker == "angel_one":
        from src.broker.angel_one import AngelOneAdapter
        return AngelOneAdapter()

    raise ValueError(
        f"Unknown broker '{broker}'. Set BROKER=angel_one or BROKER=zerodha in .env."
    )
