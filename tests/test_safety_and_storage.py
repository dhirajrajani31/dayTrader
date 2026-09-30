import pytest
from sqlalchemy import inspect

from app.config.settings import Settings
from app.storage.database import create_database
from app.storage.models import Base


def test_non_shadow_mode_refuses_configuration():
    with pytest.raises(ValueError):
        Settings(trading_mode="LIVE")


def test_tastytrade_environment_selects_documented_base_url():
    assert Settings(tastytrade_environment="production").tastytrade_base_url == (
        "https://api.tastyworks.com"
    )
    assert Settings(tastytrade_environment="sandbox").tastytrade_base_url == (
        "https://api.cert.tastyworks.com"
    )


def test_refresh_credentials_satisfy_runtime_configuration():
    settings = Settings(tastytrade_client_secret="secret", tastytrade_refresh_token="refresh")
    assert settings.has_tastytrade_credentials


def test_all_required_sqlite_tables_initialize(tmp_path):
    engine, _ = create_database(f"sqlite:///{tmp_path / 'pilot.db'}")
    names = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= names
    assert {
        "watchlist_sessions",
        "market_feature_snapshots",
        "strategy_states",
        "state_transitions",
        "signals",
        "shadow_trades",
        "shadow_trade_outcomes",
        "application_events",
    } <= names
