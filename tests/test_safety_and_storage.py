import pytest
from sqlalchemy import inspect

from app.alerts.telegram import AlertDispatcher
from app.config.settings import Settings
from app.main import demo
from app.storage.database import create_database
from app.storage.models import Base
from app.storage.repository import Repository


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


def test_option_policy_can_be_configured_from_environment(monkeypatch):
    monkeypatch.setenv("OPTION_MIN_DAYS_TO_EXPIRATION", "2")
    monkeypatch.setenv("OPTION_MAXIMUM_SPREAD_PCT", "0.15")

    settings = Settings()

    assert settings.options.min_days_to_expiration == 2
    assert settings.options.maximum_spread_pct == 0.15


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
        "shadow_option_trades",
        "shadow_option_marks",
        "shadow_execution_events",
        "application_events",
    } <= names


@pytest.mark.asyncio
async def test_demo_never_uses_configured_telegram_credentials(tmp_path, monkeypatch):
    settings = Settings(
        telegram_bot_token="configured-secret",
        telegram_chat_id="configured-chat",
        database_url=f"sqlite:///{tmp_path / 'demo.db'}",
    )
    _, sessions = create_database(settings.database_url)
    dispatchers: list[tuple[str | None, str | None]] = []

    async def capture_delivery(self, transition, option=None):
        dispatchers.append((self.token, self.chat_id))
        return True

    monkeypatch.setattr(AlertDispatcher, "send", capture_delivery)

    await demo(settings, Repository(sessions))

    assert dispatchers
    assert set(dispatchers) == {(None, None)}
