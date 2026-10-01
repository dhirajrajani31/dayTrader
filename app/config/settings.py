from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrategySettings(BaseSettings):
    """Versioned rule thresholds. Values are intentionally configurable, not optimized."""

    model_config = SettingsConfigDict(
        env_prefix="STRATEGY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    name: str = "MR_INVESTR_BASELINE"
    version: str = "0.2.0"
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
    candidate_log_minimum_checks: int = Field(default=3, ge=1, le=5)
    candidate_log_cooldown_seconds: int = Field(default=300, ge=0)
    stale_after_seconds: int = 30


class OptionSettings(BaseSettings):
    """Conservative, deterministic assumptions for one-contract shadow option trades."""

    model_config = SettingsConfigDict(
        env_prefix="OPTION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    execution_policy_version: str = "OPTION_SHADOW_V1"
    min_days_to_expiration: int = Field(default=1, ge=0)
    max_days_to_expiration: int = Field(default=7, ge=1)
    maximum_chain_strikes: int = Field(default=20, ge=4, le=50)
    minimum_abs_delta: float = Field(default=0.55, ge=0, le=1)
    maximum_abs_delta: float = Field(default=0.70, ge=0, le=1)
    target_abs_delta: float = Field(default=0.625, ge=0, le=1)
    maximum_spread_pct: float = Field(default=0.20, gt=0, le=1)
    quantity: int = Field(default=1, ge=1)
    contract_multiplier: int = Field(default=100, ge=1)
    maximum_holding_minutes: int = Field(default=60, ge=5)
    opening_commission_per_contract: float = Field(default=1.00, ge=0)
    estimated_opening_fees_per_contract: float = Field(default=0.15, ge=0)
    estimated_closing_fees_per_contract: float = Field(default=0.15, ge=0)
    additional_slippage_price_per_side: float = Field(default=0.01, ge=0)
    execution_delay_tolerance_seconds: int = Field(default=15, ge=0)
    maximum_option_quote_age_seconds: int = Field(default=30, ge=1)

    @field_validator("maximum_abs_delta")
    @classmethod
    def maximum_delta_not_below_minimum(cls, value: float, info) -> float:
        minimum = info.data.get("minimum_abs_delta")
        if minimum is not None and value < minimum:
            raise ValueError("maximum_abs_delta must be at least minimum_abs_delta")
        return value

    @field_validator("max_days_to_expiration")
    @classmethod
    def maximum_dte_not_below_minimum(cls, value: int, info) -> int:
        minimum = info.data.get("min_days_to_expiration")
        if minimum is not None and value < minimum:
            raise ValueError("max_days_to_expiration must be at least min_days_to_expiration")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    trading_mode: Literal["SHADOW"] = "SHADOW"
    timezone: str = "America/Chicago"
    database_url: str = "sqlite:///data/tradingpilot.db"
    watchlist_file: Path = Path("watchlist.txt")
    watchlist_context_file: Path = Path("watchlist_context.json")
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
    options: OptionSettings = Field(default_factory=OptionSettings)

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
