from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def _money(value: object) -> str:
    number = _coerce_float(value)
    return "n/a" if number is None else f"${number:,.2f}"


def _percent(value: object) -> str:
    number = _coerce_float(value)
    return "n/a" if number is None else f"{number:+.1%}"


def _number(value: object, digits: int = 2) -> str:
    number = _coerce_float(value)
    return "n/a" if number is None else f"{number:.{digits}f}"


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _when(value: object) -> str:
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M CT")


def format_trade_report(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "No shadow trades recorded."
    sections: list[str] = []
    for row in rows:
        outcome = row.get("underlying_outcome") or {}
        assert isinstance(outcome, dict)
        option = row.get("option")
        lines = [
            (
                f"#{row['id']} {row['symbol']} | {str(row['direction']).title()} | "
                f"{_when(row['opened_at'])}"
            ),
            (
                f"  Underlying: entry {_money(row['entry_underlying'])} | "
                f"stop {_money(row['invalidation_underlying'])} | "
                f"T1 {_money(row['target_1'])} | T2 {_money(row['target_2'])}"
            ),
            (
                f"  Outcome: MFE {_money(outcome.get('mfe'))} | "
                f"MAE {_money(outcome.get('mae'))} | "
                f"stop {'yes' if outcome.get('stop_hit') else 'no'} | "
                f"T1 {'yes' if outcome.get('target_1_hit') else 'no'} | "
                f"T2 {'yes' if outcome.get('target_2_hit') else 'no'} | "
                f"R {_number(outcome.get('estimated_r_multiple'))}"
            ),
        ]
        if not isinstance(option, dict):
            lines.append("  Option: unavailable for this signal")
        else:
            latest = option.get("latest_mark") or {}
            assert isinstance(latest, dict)
            lines.extend(
                [
                    (
                        f"  Option: {option['symbol']} | {option['call_put']} "
                        f"${_number(option['strike'])} | exp {_when(option['expiration'])}"
                    ),
                    (
                        f"  Entry: {_money(option['entry_fill'])} at ask | "
                        f"delta {_number(option.get('entry_delta'), 3)} | "
                        f"theta {_number(option.get('entry_theta'), 3)} | "
                        f"IV {_percent(option.get('entry_iv'))} | "
                        f"OI {option.get('entry_open_interest') or 0:,}"
                    ),
                    (
                        f"  Latest liquidation: {_money(latest.get('liquidation_price'))} | "
                        f"net P&L {_money(latest.get('net_unrealized_pnl'))} "
                        f"({_percent(latest.get('net_unrealized_return_pct'))}) | "
                        f"gross {_money(latest.get('unrealized_pnl'))}"
                    ),
                    (
                        f"  Status: {option['status']}"
                        + (
                            f" | {option.get('exit_reason')} | realized "
                            f"net {_money(latest.get('net_realized_pnl'))} "
                            f"({_percent(latest.get('net_realized_return_pct'))}) | "
                            f"gross {_money(option.get('realized_pnl'))}"
                            if option.get("status") == "CLOSED"
                            else ""
                        )
                    ),
                    (
                        "  Estimated round-trip friction: "
                        f"{_money(latest.get('estimated_round_trip_cost'))}"
                    ),
                    f"  Execution policy: {option['execution_policy_version']}",
                ]
            )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
