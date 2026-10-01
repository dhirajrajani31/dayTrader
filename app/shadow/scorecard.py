from __future__ import annotations

from datetime import date, datetime, timedelta
from math import inf
from zoneinfo import ZoneInfo

from pydantic import BaseModel


class EvaluationScorecard(BaseModel):
    period: str
    armed_setups: int
    triggers: int
    option_trades: int
    closed_trades: int
    open_trades: int
    missed_entries: int
    delayed_entries: int
    unclassified_no_entry: int
    missed_exit_trades: int
    delayed_exit_trades: int
    mark_attempts: int
    missing_or_stale_marks: int
    wins: int
    losses: int
    breakevens: int
    unscored_closed_trades: int
    win_rate: float | None
    gross_pnl: float
    net_pnl: float
    expectancy: float | None
    average_win: float | None
    average_loss: float | None
    profit_factor: float | None
    maximum_drawdown: float
    target_exits: int
    stop_exits: int
    time_exits: int
    execution_issue_reasons: dict[str, int]

    @property
    def entry_coverage(self) -> float | None:
        return self.option_trades / self.triggers if self.triggers else None

    @property
    def mark_quality_rate(self) -> float | None:
        if not self.mark_attempts:
            return None
        return (self.mark_attempts - self.missing_or_stale_marks) / self.mark_attempts


def build_scorecards(
    dataset: dict[str, list[dict[str, object]]],
    timezone: ZoneInfo,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[EvaluationScorecard]:
    dates = sorted(
        {
            local_date(row["timestamp"], timezone)
            for row in dataset["signals"]
            if isinstance(row.get("timestamp"), datetime)
        }
        | {
            local_date(row["signal_at"], timezone)
            for row in dataset["option_trades"]
            if isinstance(row.get("signal_at"), datetime)
        }
        | {
            local_date(row["timestamp"], timezone)
            for row in dataset["transitions"]
            if isinstance(row.get("timestamp"), datetime)
        }
        | {
            local_date(row["timestamp"], timezone)
            for row in dataset["execution_events"]
            if isinstance(row.get("timestamp"), datetime)
        }
    )
    if start is not None and end is not None and start == end and start not in dates:
        dates.append(start)
    selected = [
        day
        for day in sorted(dates)
        if (start is None or day >= start) and (end is None or day <= end)
    ]
    cards = [_summarize(dataset, timezone, {day}, day.isoformat()) for day in selected]
    if len(selected) > 1:
        cards.append(_summarize(dataset, timezone, set(selected), "TOTAL"))
    return cards


def _summarize(
    dataset: dict[str, list[dict[str, object]]],
    timezone: ZoneInfo,
    dates: set[date],
    period: str,
) -> EvaluationScorecard:
    signals = [
        row
        for row in dataset["signals"]
        if _row_date(row, "timestamp", timezone) in dates
    ]
    transitions = [
        row
        for row in dataset["transitions"]
        if _row_date(row, "timestamp", timezone) in dates
    ]
    trades = [
        row
        for row in dataset["option_trades"]
        if _row_date(row, "signal_at", timezone) in dates
    ]
    trade_ids = {
        identifier
        for row in trades
        if isinstance((identifier := row.get("id")), int)
    }
    events = [
        row
        for row in dataset["execution_events"]
        if row.get("shadow_option_trade_id") in trade_ids
        or (
            row.get("shadow_option_trade_id") is None
            and _row_date(row, "timestamp", timezone) in dates
        )
    ]
    marks = [
        row
        for row in dataset["option_marks"]
        if row.get("shadow_option_trade_id") in trade_ids
    ]
    closed = [row for row in trades if row.get("status") == "CLOSED"]
    pnls = [_trade_pnl(row) for row in closed]
    net_pnls = [net for _, net in pnls if net is not None]
    gross_pnls = [gross for gross, _ in pnls if gross is not None]
    wins = [value for value in net_pnls if value > 0]
    losses = [value for value in net_pnls if value < 0]
    breakevens = [value for value in net_pnls if value == 0]
    missed_entry_events = _events(events, "ENTRY", "MISSED")
    delayed_entry_events = _events(events, "ENTRY", "DELAYED")
    missed_exit_ids = {
        row.get("shadow_option_trade_id")
        for row in _events(events, "EXIT", "MISSED")
        if row.get("shadow_option_trade_id") is not None
    }
    delayed_exit_ids = {
        row.get("shadow_option_trade_id")
        for row in _events(events, "EXIT", "DELAYED")
        if row.get("shadow_option_trade_id") is not None
    }
    positive_sum = sum(wins)
    negative_sum = abs(sum(losses))
    profit_factor = positive_sum / negative_sum if negative_sum else (inf if wins else None)
    return EvaluationScorecard(
        period=period,
        armed_setups=sum(row.get("to_state") == "ARMED" for row in transitions),
        triggers=len(signals),
        option_trades=len(trades),
        closed_trades=len(closed),
        open_trades=len(trades) - len(closed),
        missed_entries=len(missed_entry_events),
        delayed_entries=len(delayed_entry_events),
        unclassified_no_entry=max(len(signals) - len(trades) - len(missed_entry_events), 0),
        missed_exit_trades=len(missed_exit_ids),
        delayed_exit_trades=len(delayed_exit_ids),
        mark_attempts=len(marks),
        missing_or_stale_marks=sum(row.get("mark_quality") != "READY" for row in marks),
        wins=len(wins),
        losses=len(losses),
        breakevens=len(breakevens),
        unscored_closed_trades=len(closed) - len(net_pnls),
        win_rate=len(wins) / (len(wins) + len(losses)) if wins or losses else None,
        gross_pnl=sum(gross_pnls),
        net_pnl=sum(net_pnls),
        expectancy=sum(net_pnls) / len(net_pnls) if net_pnls else None,
        average_win=sum(wins) / len(wins) if wins else None,
        average_loss=sum(losses) / len(losses) if losses else None,
        profit_factor=profit_factor,
        maximum_drawdown=_maximum_drawdown(net_pnls),
        target_exits=sum(row.get("exit_reason") == "UNDERLYING_TARGET_1" for row in closed),
        stop_exits=sum(row.get("exit_reason") == "UNDERLYING_INVALIDATION" for row in closed),
        time_exits=sum(row.get("exit_reason") == "TIME_EXIT" for row in closed),
        execution_issue_reasons=_issue_reasons(events),
    )


def format_scorecards(cards: list[EvaluationScorecard]) -> str:
    if not cards:
        return "No shadow evaluation data for the selected period."
    return "\n\n".join(_format_card(card) for card in cards)


def _format_card(card: EvaluationScorecard) -> str:
    operational = "CLEAN"
    if card.unclassified_no_entry or card.missed_exit_trades or card.unscored_closed_trades:
        operational = "INVESTIGATE"
    elif card.missed_entries or card.delayed_entries or card.delayed_exit_trades:
        operational = "DEGRADED"
    evidence = f"{card.closed_trades}/200 closed trades"
    return "\n".join(
        [
            f"SHADOW SCORECARD | {card.period}",
            (
                f"Funnel: {card.armed_setups} armed | {card.triggers} triggers | "
                f"{card.option_trades} option trades | {card.closed_trades} closed | "
                f"{card.open_trades} open"
            ),
            (
                f"Entry: {_pct(card.entry_coverage)} coverage | {card.missed_entries} missed | "
                f"{card.delayed_entries} delayed | "
                f"{card.unclassified_no_entry} unclassified"
            ),
            (
                f"Results: {card.wins}W/{card.losses}L/{card.breakevens}BE | "
                f"{card.unscored_closed_trades} unscored | "
                f"win rate {_pct(card.win_rate)} | net {_money(card.net_pnl)} | "
                f"gross {_money(card.gross_pnl)}"
            ),
            (
                f"Quality: expectancy {_money(card.expectancy)} | "
                f"profit factor {_number(card.profit_factor)} | "
                f"max drawdown {_money(card.maximum_drawdown)}"
            ),
            (
                f"Exits: {card.target_exits} target | {card.stop_exits} stop | "
                f"{card.time_exits} time | {card.missed_exit_trades} missed | "
                f"{card.delayed_exit_trades} delayed"
            ),
            (
                f"Marks: {card.mark_attempts} attempts | quality "
                f"{_pct(card.mark_quality_rate)} | "
                f"{card.missing_or_stale_marks} missing/stale"
            ),
            f"Operational status: {operational} | Evidence gate: {evidence}",
        ]
        + (
            [
                "Issue reasons: "
                + ", ".join(
                    f"{reason}={count}"
                    for reason, count in sorted(card.execution_issue_reasons.items())
                )
            ]
            if card.execution_issue_reasons
            else []
        )
    )


def _trade_pnl(row: dict[str, object]) -> tuple[float | None, float | None]:
    mark = row.get("latest_mark")
    payload = mark if isinstance(mark, dict) else {}
    gross = _float(payload.get("realized_pnl", row.get("gross_realized_pnl")))
    net = _float(payload.get("net_realized_pnl"))
    return gross, net


def _events(
    rows: list[dict[str, object]], stage: str, status: str
) -> list[dict[str, object]]:
    return [row for row in rows if row.get("stage") == stage and row.get("status") == status]


def _issue_reasons(rows: list[dict[str, object]]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    for row in rows:
        if row.get("status") not in {"MISSED", "DELAYED"}:
            continue
        reason = str(row.get("reason_code") or "UNKNOWN")
        reasons[reason] = reasons.get(reason, 0) + 1
    return reasons


def _maximum_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _row_date(row: dict[str, object], field: str, timezone: ZoneInfo) -> date | None:
    value = row.get(field)
    return local_date(value, timezone) if isinstance(value, datetime) else None


def local_date(value: object, timezone: ZoneInfo) -> date:
    if not isinstance(value, datetime):
        raise TypeError("scorecard timestamps must be datetime values")
    return value.astimezone(timezone).date()


def _float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _number(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value == inf:
        return "∞"
    return f"{value:.2f}"


def default_start(today: date, days: int) -> date:
    return today - timedelta(days=max(days - 1, 0))
