from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrategySettings(BaseSettings):
    """Versioned rule thresholds. Values are intentionally configurable, not optimized."""

    model_config = SettingsConfigDict(env_prefix="STRATEGY_", extra="ignore")

    name: str = "MR_INVESTR_BASELINE"
    version: str = "0.1.0"
    priority_monitoring_start: time = time(8, 35)
    priority_monitoring_end: time = time(10, 30)
    minimum_relative_volume: float = 1.5
    minimum_reward_risk: float = 1.5
    approach_distance_pct: float = 0.003
    breakout_buffer_pct: float = 0.0005
    retest_tolerance_pct: float = 0.002
    confirmation_distance_pct: float = 0.0005
    extension_threshold_pct: float = 0.012
    invalidation_buffer_pct: float = 0.002
    alert_cooldown_seconds: int = 300
    stale_after_seconds: int = 30


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    trading_mode: Literal["SHADOW"] = "SHADOW"
    timezone: str = "America/Chicago"
    database_url: str = "sqlite:///data/tradingpilot.db"
    watchlist_file: Path = Path("watchlist.txt")
    log_file: Path = Path("logs/tradingpilot.log")
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    tastytrade_environment: Literal["production", "sandbox"] = "production"
    tastytrade_api_url: str | None = None
    tastytrade_access_token: str | None = None
    tastytrade_client_id: str | None = None
    tastytrade_client_secret: str | None = None
    tastytrade_refresh_token: str | None = None
    historical_lookback_days: int = 5
    historical_warmup_timeout_seconds: float = 20
    benchmarks: tuple[str, ...] = ("SPY", "QQQ")
    strategy: StrategySettings = Field(default_factory=StrategySettings)

    @field_validator("trading_mode", mode="before")
    @classmethod
    def shadow_only(cls, value: object) -> str:
        if str(value).upper() != "SHADOW":
            raise ValueError("TradingPilot is shadow-only; TRADING_MODE must be SHADOW")
        return "SHADOW"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def assert_shadow_mode(self) -> None:
        if self.trading_mode != "SHADOW":  # defense in depth for non-Pydantic construction
            raise RuntimeError("Execution disabled: TradingPilot requires SHADOW mode")

    @property
    def tastytrade_base_url(self) -> str:
        if self.tastytrade_api_url:
            return self.tastytrade_api_url.rstrip("/")
        if self.tastytrade_environment == "sandbox":
            return "https://api.cert.tastyworks.com"
        return "https://api.tastyworks.com"

    @property
    def has_tastytrade_credentials(self) -> bool:
        refresh_credentials = bool(self.tastytrade_client_secret and self.tastytrade_refresh_token)
        return bool(self.tastytrade_access_token or refresh_credentials)
