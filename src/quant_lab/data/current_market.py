"""Incremental current-market refresh, independent of WRDS interactive login."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from quant_lab.data.baostock_client import DAILY_FIELDS, symbol_to_baostock
from quant_lab.data.baostock_supplement import normalize_raw, refresh_benchmarks
from quant_lab.data.daily_update import _with_retry
from quant_lab.data.freshness import session_target, shanghai_now, validate_latest_panel
from quant_lab.data.index_membership import validate_snapshot
from quant_lab.data.market_staging import (
    _register_baostock,
    merge_raw_sources,
    normalize_csmar,
)
from quant_lab.data.research_prices import prepare_price_panel
from quant_lab.data.storage_budget import StorageBudget, write_budgeted_frame


def load_local_raw(root: Path, first, last, symbols: set[str]) -> pd.DataFrame:
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='1GB'")
        connection.execute("SET threads=2")
        _register_baostock(connection, root)
        wanted = pd.DataFrame({"symbol": sorted(symbols)})
        connection.register("wanted", wanted)
        bao = connection.execute(
            "SELECT b.* FROM bao b JOIN wanted USING(symbol) WHERE trade_date BETWEEN ? AND ?",
            [pd.Timestamp(first), pd.Timestamp(last)],
        ).df()
        bao = normalize_raw(bao)
        paths = [str(p) for p in (root / "data/raw/csmar/bulk/csmar_trade/trd_dalyr").glob("*.parquet")]
        if not paths:
            raise ValueError("CSMAR source partitions are missing")
        connection.read_parquet(paths, union_by_name=True).create_view("cs")
        cs = connection.execute(
            "SELECT cs.* FROM cs JOIN wanted ON stkcd=substr(wanted.symbol,1,6) WHERE CAST(trddt AS DATE) BETWEEN ? AND ?",
            [pd.Timestamp(first), pd.Timestamp(last)],
        ).df()
    cs = normalize_csmar(cs, {s.split(".")[0]: s for s in symbols})
    merged, _ = merge_raw_sources(cs, bao)
    return merged


def refresh_current_market(
    client, root: Path, budget: StorageBudget, *, now=None, preferred_index="000905.SH",
    holdings: set[str] | None = None, ready_hour=18, lookback_sessions=260, progress=print,
) -> tuple[pd.DataFrame, dict]:
    now = pd.Timestamp(now if now is not None else shanghai_now())
    if now.tzinfo is not None:
        now = now.tz_convert("Asia/Shanghai").tz_localize(None)
    if lookback_sessions < 130:
        raise ValueError("Current factor configuration needs at least 130 sessions")
    base = root / "data/raw/baostock_supplement"
    start = (now - pd.Timedelta(days=lookback_sessions * 2 + 120)).strftime("%Y-%m-%d")
    progress("更新交易日历", flush=True)
    calendar = _with_retry(client, lambda: client.trade_calendar(start, (now + pd.Timedelta(days=40)).strftime("%Y-%m-%d")), attempts=3)
    target, upcoming = session_target(calendar, now, ready_hour)
    write_budgeted_frame(calendar, base / "reference/current_calendar.parquet", budget)
    sessions = pd.DatetimeIndex(calendar.loc[calendar["is_trading_day"], "trade_date"])
    historical = root / "data/raw/market_reference/trade_calendar.parquet"
    if historical.exists():
        all_calendar = pd.concat([pd.read_parquet(historical), calendar]).drop_duplicates("trade_date", keep="last").sort_values("trade_date")
        write_budgeted_frame(all_calendar, historical, budget)
    window = sessions[sessions <= target][-lookback_sessions:]
    if len(window) < lookback_sessions:
        raise ValueError("Insufficient calendar for the factor lookback")
    first = window.min()
    reference = root / "data/raw/market_reference"
    membership_frames = []
    for code in ["000300.SH", "000905.SH"]:
        progress(f"核对{code}当前成分股", flush=True)
        snapshot = validate_snapshot(_with_retry(client, lambda code=code: client.index_snapshot(code, str(target.date())), attempts=3), code, target)
        snapshot["index_code"] = code
        snapshot["snapshot_complete"] = True
        snapshot["source"] = "baostock"
        snapshot["membership_quality"] = "periodic_provider_snapshot"
        membership_frames.append(snapshot)
    current = pd.concat(membership_frames, ignore_index=True)
    write_budgeted_frame(current, base / "reference/current_membership.parquet", budget)
    research_symbols = set(current.loc[current["index_code"].eq(preferred_index), "symbol"])
    if not research_symbols:
        raise ValueError("Unsupported research index")
    required = research_symbols | (holdings or set())
    all_symbols_path = reference / "historical_symbols.parquet"
    scope = set(pd.read_parquet(all_symbols_path)["symbol"]) | set(current["symbol"]) | required
    write_budgeted_frame(pd.DataFrame({"symbol": sorted(scope)}), all_symbols_path, budget)
    old_members = pd.read_parquet(reference / "index_membership.parquet")
    # A corrected snapshot replaces the entire index/date slice; a row-wise
    # union would keep constituents that the provider has removed.
    old_members["trade_date"] = pd.to_datetime(old_members["trade_date"])
    current["trade_date"] = pd.to_datetime(current["trade_date"])
    refreshed_keys = pd.MultiIndex.from_frame(current[["index_code", "trade_date"]].drop_duplicates())
    old_keys = pd.MultiIndex.from_frame(old_members[["index_code", "trade_date"]])
    old_members = old_members.loc[~old_keys.isin(refreshed_keys)]
    combined_members = pd.concat([old_members, current]).drop_duplicates(["index_code", "trade_date", "symbol"], keep="last")
    write_budgeted_frame(combined_members.sort_values(["index_code", "trade_date", "symbol"]), reference / "index_membership.parquet", budget)
    progress("更新股票上市与退市信息", flush=True)
    basics = _with_retry(client, lambda: client._to_frame(client.bs.query_stock_basic(), "stock_basic"), attempts=3)
    write_budgeted_frame(basics, base / "reference/stock_basic.parquet", budget)
    # Always re-query the latest completed session, even if a prior run cached
    # partial data. Common missing dates use one full-market request per day.
    local = load_local_raw(root, first, target, required)
    observed = local.groupby("trade_date")["symbol"].nunique()
    recent = window[-20:]
    days = sorted(set(recent[~recent.isin(observed[observed.eq(len(required))].index)]) | {target})
    downloaded = 0
    for day in days:
        progress(f"更新{day.date()}日行情", flush=True)
        frame = normalize_raw(_with_retry(client, lambda day=day: client._to_frame(client.bs.query_daily_history_k_AStock(str(day.date())), "daily_cross_section"), attempts=3))
        if frame.empty or not frame["trade_date"].eq(day).all():
            raise ValueError("Provider has not published the requested session")
        frame = frame.loc[frame["symbol"].isin(scope)]
        write_budgeted_frame(frame, base / "cross_sections" / f"{day.date()}.parquet", budget)
        downloaded += len(frame)
    local = load_local_raw(root, first, target, required)
    # New members or a sparse old cache need their complete rolling window.
    ipo_by_code = basics.set_index("code")["ipoDate"].to_dict()
    for number, symbol in enumerate(sorted(required), start=1):
        rows = local.loc[local["symbol"].eq(symbol)]
        ipo = pd.to_datetime(ipo_by_code.get(symbol_to_baostock(symbol)), errors="coerce")
        expected = window[window >= ipo] if pd.notna(ipo) else window
        missing = expected.difference(pd.DatetimeIndex(rows["trade_date"]))
        unknown = not rows.empty and not (rows["is_st_known"] & rows["is_suspended_known"]).all()
        if not len(missing) and not unknown:
            continue
        progress(f"补齐当前股票池历史 {number}/{len(required)}：{symbol}", flush=True)
        incoming = normalize_raw(_with_retry(client, lambda symbol=symbol: client._to_frame(client.bs.query_history_k_data_plus(
            symbol_to_baostock(symbol), DAILY_FIELDS, start_date=str(first.date()), end_date=str(target.date()), frequency="d", adjustflag="3"), "current_history"), attempts=3))
        if not incoming["symbol"].eq(symbol).all() or not incoming["trade_date"].between(first, target).all():
            raise ValueError("Unrequested symbol/date")
        path = base / "raw_daily" / f"{symbol.replace('.', '_')}.parquet"
        cached = normalize_raw(pd.read_parquet(path)) if path.exists() else incoming.iloc[0:0]
        write_budgeted_frame(pd.concat([cached, incoming]).drop_duplicates(["trade_date", "symbol"], keep="last"), path, budget)
        downloaded += len(incoming)
    local = load_local_raw(root, first, target, required)
    market = prepare_price_panel(local, sessions, basics)
    market["in_index"] = market["symbol"].isin(research_symbols)
    gate = validate_latest_panel(market, target, required, sessions)
    progress("同步沪深300与中证500指数行情", flush=True)
    benchmarks = refresh_benchmarks(client, base, sessions, first, target, budget, refresh_latest=True)
    gate.update({"data_through": str(target.date()), "next_trade_date": str(upcoming.date()),
                 "benchmarks": benchmarks,
                 "downloaded_rows": downloaded, "preferred_index": preferred_index,
                 "research_symbols": len(research_symbols), "holdings_symbols": len(holdings or set()),
                 "lookback_sessions": lookback_sessions, "mode": "current_members_for_latest_signals_only"})
    return market, gate
