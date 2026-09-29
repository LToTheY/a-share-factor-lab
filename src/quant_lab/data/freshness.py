"""Fail-closed, exchange-calendar-aware validation for current paper signals."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.data.daily_update import latest_completed_session


def shanghai_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="Asia/Shanghai")


def session_target(calendar: pd.DataFrame, now=None, ready_hour: int = 18) -> tuple[pd.Timestamp, pd.Timestamp]:
    target = latest_completed_session(calendar, now if now is not None else shanghai_now(), ready_hour)
    future = pd.to_datetime(calendar.loc[calendar["is_trading_day"], "trade_date"])
    future = future[future > target].sort_values()
    if future.empty:
        raise ValueError("Trading calendar does not contain the next session")
    return target, pd.Timestamp(future.iloc[0])


def validate_latest_panel(
    market: pd.DataFrame, target: pd.Timestamp, required_symbols: set[str],
    sessions: pd.DatetimeIndex, *, lookback: int = 121,
) -> dict:
    """A latest-day island cannot pass when factor-window sessions are missing."""
    errors = []
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback <= 0:
        raise ValueError("因子观察窗口必须为正整数")
    sessions = pd.DatetimeIndex(sessions).dropna().unique().sort_values()
    if not required_symbols:
        errors.append("股票池为空")
    if market.duplicated(["trade_date", "symbol"]).any():
        errors.append("股票日期主键重复")
    latest = market.loc[market["trade_date"].eq(target)]
    missing = sorted(required_symbols - set(latest["symbol"]))
    if missing:
        errors.append(f"最新交易日缺少{len(missing)}只股票")
    window = sessions[sessions <= target][-lookback:]
    if len(window) < lookback or target not in sessions:
        errors.append(f"交易日历不足以覆盖完整的 {lookback} 日因子观察窗口")
    gaps = []
    bad_latest = []
    for symbol in sorted(required_symbols):
        rows = market.loc[market["symbol"].eq(symbol)]
        if rows.empty:
            continue
        dates = pd.DatetimeIndex(rows["trade_date"])
        # Newly listed companies are excluded by the universe age filter, but
        # must still have valid current quotes. No future listing date is used.
        ipo = rows["list_date"].dropna().min() if "list_date" in rows else pd.NaT
        expected = window[window >= ipo] if pd.notna(ipo) else window
        absent = expected.difference(dates)
        invalid = rows.loc[rows["trade_date"].isin(expected)]
        required = ["open", "high", "low", "close", "preclose", "adj_close", "volume", "amount"]
        broken = invalid[required].isna().any(axis=1)
        broken |= ~np.isfinite(invalid[required]).all(axis=1)
        broken |= invalid[["open", "high", "low", "close", "preclose", "adj_close"]].le(0).any(axis=1)
        broken |= invalid[["volume", "amount"]].lt(0).any(axis=1)
        broken |= invalid["high"].lt(invalid[["open", "close", "low"]].max(axis=1))
        broken |= invalid["low"].gt(invalid[["open", "close", "high"]].min(axis=1))
        broken |= ~invalid["is_suspended"] & invalid[["volume", "amount"]].le(0).any(axis=1)
        if "amount_outside_price_range" in invalid:
            broken |= invalid["amount_outside_price_range"].fillna(True)
        if len(absent) or broken.any():
            gaps.append({"symbol": symbol, "missing_sessions": len(absent), "invalid_rows": int(broken.sum())})
        day = rows.loc[rows["trade_date"].eq(target)]
        flags = ["is_st_known", "is_suspended_known", "limit_status_known"]
        if not day.empty and (any(c not in day for c in flags) or not day[flags].fillna(False).eq(True).all(axis=None)):
            bad_latest.append(symbol)
    if gaps:
        errors.append(f"{len(gaps)}只股票的因子观察窗口存在行情缺口或异常")
    if bad_latest:
        errors.append(f"{len(bad_latest)}只股票的最新交易状态未知")
    return {"passed": not errors, "expected_session": str(target.date()),
            "required_symbols": len(required_symbols), "latest_symbols": len(required_symbols & set(latest["symbol"])),
            "missing_symbols": missing, "window_gaps": gaps, "unknown_status_symbols": bad_latest,
            "errors": errors}


def current_result_status(status: dict, calendar: pd.DataFrame, now=None, ready_hour: int = 18) -> tuple[bool, str]:
    """Re-evaluate persisted success when the dashboard is opened on a later day."""
    if status.get("status") != "ready":
        return False, str(status.get("message", "尚未完成本次数据更新与检查"))
    try:
        expected, upcoming = session_target(calendar, now, ready_hour)
    except (ValueError, RuntimeError, KeyError):
        return False, "交易日历需要更新，暂不能确认结果是否最新"
    if status.get("data_through") != str(expected.date()):
        return False, f"结果已过期：应使用{expected.date()}的日行情，请先更新"
    if status.get("next_trade_date") != str(upcoming.date()):
        return False, "下一交易日不一致，请重新检查"
    return True, "已使用最新可用的完整日行情；建议仅供人工复核"
