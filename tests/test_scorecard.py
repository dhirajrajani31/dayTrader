from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.shadow.scorecard import build_scorecards, format_scorecards


def test_daily_scorecard_calculates_funnel_net_results_and_execution_quality():
    start = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
    dataset = {
        "signals": [
            {"id": index, "timestamp": start + timedelta(minutes=index)}
            for index in range(3)
        ],
        "transitions": [
            {"timestamp": start + timedelta(minutes=index), "to_state": "ARMED"}
            for index in range(3)
        ],
        "option_trades": [
            {
                "id": 10,
                "signal_at": start,
                "status": "CLOSED",
                "exit_reason": "UNDERLYING_TARGET_1",
                "gross_realized_pnl": 100.0,
                "latest_mark": {"realized_pnl": 100.0, "net_realized_pnl": 90.0},
            },
            {
                "id": 11,
                "signal_at": start + timedelta(minutes=1),
                "status": "CLOSED",
                "exit_reason": "UNDERLYING_INVALIDATION",
                "gross_realized_pnl": -50.0,
                "latest_mark": {"realized_pnl": -50.0, "net_realized_pnl": -55.0},
            },
        ],
        "execution_events": [
            {
                "shadow_option_trade_id": None,
                "timestamp": start + timedelta(minutes=2),
                "stage": "ENTRY",
                "status": "MISSED",
                "reason_code": "NO_CONTRACT_PASSED_FILTERS",
            },
            {
                "shadow_option_trade_id": 10,
                "timestamp": start,
                "stage": "ENTRY",
                "status": "DELAYED",
                "reason_code": "LATE_OPTION_SNAPSHOT",
            },
            {
                "shadow_option_trade_id": 11,
                "timestamp": start + timedelta(minutes=10),
                "stage": "EXIT",
                "status": "MISSED",
                "reason_code": "NO_OPTION_QUOTE",
            },
            {
                "shadow_option_trade_id": 11,
                "timestamp": start + timedelta(minutes=11),
                "stage": "EXIT",
                "status": "DELAYED",
                "reason_code": "LATE_VALID_QUOTE",
            },
        ],
        "option_marks": [
            {"shadow_option_trade_id": 10, "mark_quality": "READY"},
            {"shadow_option_trade_id": 10, "mark_quality": "READY"},
            {"shadow_option_trade_id": 11, "mark_quality": "STALE_OPTION_QUOTE"},
            {"shadow_option_trade_id": 11, "mark_quality": "READY"},
        ],
    }

    cards = build_scorecards(
        dataset, ZoneInfo("America/Chicago"), start=date(2026, 10, 1), end=date(2026, 10, 1)
    )

    assert len(cards) == 1
    card = cards[0]
    assert card.armed_setups == 3
    assert card.triggers == 3
    assert card.option_trades == 2
    assert card.missed_entries == 1
    assert card.unclassified_no_entry == 0
    assert card.entry_coverage == pytest.approx(2 / 3)
    assert card.wins == 1 and card.losses == 1
    assert card.unscored_closed_trades == 0
    assert card.win_rate == 0.5
    assert card.gross_pnl == 50
    assert card.net_pnl == 35
    assert card.expectancy == 17.5
    assert card.profit_factor == pytest.approx(90 / 55)
    assert card.maximum_drawdown == 55
    assert card.missed_exit_trades == 1
    assert card.delayed_exit_trades == 1
    assert card.mark_quality_rate == 0.75
    assert card.execution_issue_reasons["NO_OPTION_QUOTE"] == 1
    assert "Operational status: INVESTIGATE" in format_scorecards(cards)


def test_all_history_includes_transition_only_dates_and_total():
    first = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
    second = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    dataset = {
        "signals": [],
        "transitions": [
            {"timestamp": first, "to_state": "ARMED"},
            {"timestamp": second, "to_state": "ARMED"},
        ],
        "option_trades": [],
        "execution_events": [],
        "option_marks": [],
    }

    cards = build_scorecards(dataset, ZoneInfo("America/Chicago"))

    assert [card.period for card in cards] == ["2026-09-30", "2026-10-01", "TOTAL"]
    assert cards[-1].armed_setups == 2
