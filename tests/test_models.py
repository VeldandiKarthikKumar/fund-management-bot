"""
Integration tests for DB models and repositories.
Requires a running PostgreSQL instance (provided by docker-compose in CI).
"""

import os
import pytest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, TradeSuggestion, SuggestionStatus


@pytest.fixture(scope="session")
def engine():
    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://fundbot:test@localhost:5432/fundbot_test"
    )
    eng = create_engine(db_url)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.rollback()
    s.close()


def test_create_suggestion(session):
    s = TradeSuggestion(
        symbol="RELIANCE",
        action="BUY",
        entry_price=2800.0,
        target_price=2950.0,
        stop_loss=2720.0,
        suggested_qty=10,
        risk_amount_inr=800.0,
        risk_reward=1.875,
        signals_fired=[{"signal_name": "ema_crossover", "strength": 0.75}],
        composite_score=0.75,
        timeframe="daily",
        slack_channel="#test",
    )
    session.add(s)
    session.flush()
    assert s.id is not None
    assert s.status == SuggestionStatus.PENDING


def test_suggestion_status_transitions(session):
    s = TradeSuggestion(
        symbol="TCS",
        action="SELL",
        entry_price=3800.0,
        target_price=3600.0,
        stop_loss=3900.0,
        suggested_qty=5,
        risk_amount_inr=500.0,
        risk_reward=2.0,
        signals_fired=[],
        composite_score=0.6,
        timeframe="daily",
        slack_channel="#test",
    )
    session.add(s)
    session.flush()

    s.status = SuggestionStatus.EXECUTED
    s.user_response_at = datetime.utcnow()
    session.flush()
    assert s.status == SuggestionStatus.EXECUTED
