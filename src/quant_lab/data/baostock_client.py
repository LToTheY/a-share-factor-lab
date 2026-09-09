"""Free BaoStock daily-data adapter with resumable local partitions."""

from __future__ import annotations

import json
import socket
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Self

import numpy as np
import pandas as pd

from quant_lab.data.schema import normalize_daily_frame
from quant_lab.data.storage import write_table

DAILY_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,"
    "turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"
)


def baostock_to_symbol(code: str) -> str:
    """Convert ``sh.600000`` to the project's ``600000.SH`` convention."""
    exchange, number = code.split(".", maxsplit=1)
    return f"{number}.{exchange.upper()}"


def symbol_to_baostock(symbol: str) -> str:
    number, exchange = symbol.split(".", maxsplit=1)
    return f"{exchange.lower()}.{number}"


def _round_price(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def price_limit_ratio(
    symbol: str,
    trade_date: pd.Timestamp,
    is_st: bool,
    listing_age_sessions: int | None = None,
) -> float | None:
    """Approximate exchange price-limit rules for Shanghai/Shenzhen A shares."""
    number = symbol.split(".")[0]
    newly_listed = listing_age_sessions is not None and listing_age_sessions < 5
    no_limit_new_listing = newly_listed and (
        number.startswith("688")
        or (number.startswith("300") and trade_date >= pd.Timestamp("2020-08-24"))
        or (
            number.startswith(("60", "00"))
            and trade_date >= pd.Timestamp("2023-04-10")
        )
    )
    if no_limit_new_listing:
        return None
    if is_st:
        return 0.05
    if number.startswith("688"):
        return 0.20
    if number.startswith("300") and trade_date >= pd.Timestamp("2020-08-24"):
        return 0.20
    return 0.10


def derive_open_limit_flags(
    frame: pd.DataFrame, *, adjusted_prices: bool = False
) -> pd.DataFrame:
    """Flag an opening price pinned at its theoretical daily price limit.

    Raw exchange prices can be compared with cent-rounded limit prices. For
    back-adjusted series the price scale is synthetic, so compare returns with
    the applicable limit ratio instead.
    """
    result = frame.copy()
    listing_ages = result.get(
        "listing_age_sessions", pd.Series([None] * len(result), index=result.index)
    )
    ratios = [
        price_limit_ratio(symbol, date, bool(is_st), age)
        for symbol, date, is_st, age in zip(
            result["symbol"],
            result["trade_date"],
            result["is_st"],
            listing_ages,
        )
    ]
    upper = [
        _round_price(preclose * (1 + ratio))
        if pd.notna(preclose) and ratio is not None
        else np.nan
        for preclose, ratio in zip(result["preclose"], ratios)
    ]
    lower = [
        _round_price(preclose * (1 - ratio))
        if pd.notna(preclose) and ratio is not None
        else np.nan
        for preclose, ratio in zip(result["preclose"], ratios)
    ]
    result["up_limit"] = upper
    result["down_limit"] = lower
    result["is_no_limit_session"] = pd.isna(ratios)
    if adjusted_prices:
        open_return = result["open"].div(result["preclose"]).sub(1)
        ratio_series = pd.Series(ratios, index=result.index, dtype=float)
        # Exchange tick rounding can move the observed raw return slightly away
        # from 5/10/20%; the adjustment itself preserves that return ratio.
        return_tolerance = 0.003
        result["is_limit_up"] = open_return.sub(ratio_series).abs() <= return_tolerance
        result["is_limit_down"] = (
            open_return.add(ratio_series).abs() <= return_tolerance
        )
    else:
        price_tolerance = 0.0051
        result["is_limit_up"] = result["open"].notna() & (
            (result["open"] - result["up_limit"]).abs() <= price_tolerance
        )
        result["is_limit_down"] = result["open"].notna() & (
            (result["open"] - result["down_limit"]).abs() <= price_tolerance
        )
    result["limit_status_known"] = result["preclose"].notna()
    return result


class BaoStockDownloader:
    """Logged-in adapter around BaoStock's anonymous/free Python service."""

    def __init__(self) -> None:
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError("Install with: pip install -e .[free-data]") from exc
        self.bs = bs
        self._logged_in = False

    def __enter__(self) -> Self:
        self.reconnect()
        return self

    def reconnect(self) -> None:
        """Create a fresh session after the free server closes a long connection."""
        import baostock.common.context as bs_context

        if self._logged_in:
            try:
                bs_context.default_socket.close()
            except OSError:
                pass
            bs_context.default_socket = None
            self._logged_in = False
        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(30)
        try:
            response = self.bs.login()
        finally:
            socket.setdefaulttimeout(previous_timeout)
        if response.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {response.error_msg}")
        # The upstream client otherwise performs an unbounded blocking recv().
        # A finite timeout lets the incremental retry loop recover unattended.
        bs_context.default_socket.settimeout(30)
        self._logged_in = True

    def __exit__(self, *_: object) -> None:
        if self._logged_in:
            self.bs.logout()
        self._logged_in = False

    @staticmethod
    def _to_frame(result: Any, endpoint: str) -> pd.DataFrame:
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock {endpoint} failed: {result.error_msg}")
        rows = []
        while result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=result.fields)

    def trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        result = self.bs.query_trade_dates(start_date=start_date, end_date=end_date)
        frame = self._to_frame(result, "query_trade_dates")
        frame["trade_date"] = pd.to_datetime(frame["calendar_date"])
        frame["is_trading_day"] = frame["is_trading_day"].eq("1")
        return frame[["trade_date", "is_trading_day"]]

    def zz500_snapshot(self, trade_date: str | None = None) -> pd.DataFrame:
        kwargs = {"date": trade_date} if trade_date else {}
        result = self.bs.query_zz500_stocks(**kwargs)
        frame = self._to_frame(result, "query_zz500_stocks")
        if frame.empty:
            return pd.DataFrame(columns=["trade_date", "symbol", "weight"])
        date_column = "updateDate" if "updateDate" in frame else "date"
        frame["trade_date"] = pd.to_datetime(frame[date_column])
        frame["symbol"] = frame["code"].map(baostock_to_symbol)
        frame["weight"] = np.nan
        return frame[["trade_date", "symbol", "weight"]]

    def monthly_zz500_snapshots(
        self,
        start_date: str,
        end_date: str,
        cache_dir: str | Path | None = None,
    ) -> pd.DataFrame:
        calendar = self.trade_calendar(start_date, end_date)
        open_dates = calendar.loc[calendar["is_trading_day"], "trade_date"]
        requested = open_dates.groupby(open_dates.dt.to_period("M")).max()
        cache = Path(cache_dir) if cache_dir is not None else None
        if cache is not None:
            cache.mkdir(parents=True, exist_ok=True)
        frames = []
        for number, date in enumerate(requested, start=1):
            date_text = pd.Timestamp(date).strftime("%Y-%m-%d")
            path = cache / f"zz500_{date_text}.parquet" if cache is not None else None
            if path is not None and path.exists():
                snapshot = pd.read_parquet(path)
            else:
                snapshot = self.zz500_snapshot(date_text)
                if path is not None and not snapshot.empty:
                    write_table(snapshot, path)
            if not snapshot.empty:
                # Use the provider's effective/update date, never the request date.
                frames.append(snapshot)
            print(
                f"ZZ500 snapshots: {number}/{len(requested)} ({date_text})",
                flush=True,
            )
        if not frames:
            raise RuntimeError("BaoStock returned no ZZ500 constituent snapshots")
        return (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(["trade_date", "symbol"], keep="last")
            .sort_values(["trade_date", "symbol"])
            .reset_index(drop=True)
        )

    def adjustment_factors(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        result = self.bs.query_adjust_factor(
            code=code, start_date=start_date, end_date=end_date
        )
        frame = self._to_frame(result, "query_adjust_factor")
        if frame.empty:
            return pd.DataFrame(columns=["trade_date", "adj_factor"])
        factor_column = (
            "backAdjustFactor" if "backAdjustFactor" in frame else "adjustFactor"
        )
        frame["trade_date"] = pd.to_datetime(frame["dividOperateDate"])
        frame["adj_factor"] = pd.to_numeric(frame[factor_column], errors="coerce")
        return frame[["trade_date", "adj_factor"]].dropna().sort_values("trade_date")

    def daily_history(
        self,
        code: str,
        start_date: str,
        end_date: str,
        adjustflag: str = "3",
        include_adjustment_factors: bool = True,
    ) -> pd.DataFrame:
        result = self.bs.query_history_k_data_plus(
            code,
            DAILY_FIELDS,
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag=adjustflag,
        )
        frame = self._to_frame(result, "query_history_k_data_plus")
        if frame.empty:
            return frame
        frame = frame.rename(
            columns={
                "date": "trade_date",
                "code": "provider_code",
                "turn": "turnover_rate",
                "peTTM": "pe_ttm",
                "pbMRQ": "pb_mrq",
                "psTTM": "ps_ttm",
                "pcfNcfTTM": "pcf_ncf_ttm",
            }
        )
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        frame["symbol"] = frame["provider_code"].map(baostock_to_symbol)
        numeric = [
            "open",
            "high",
            "low",
            "close",
            "preclose",
            "volume",
            "amount",
            "turnover_rate",
            "pctChg",
            "pe_ttm",
            "pb_mrq",
            "ps_ttm",
            "pcf_ncf_ttm",
        ]
        for column in numeric:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["is_suspended"] = ~frame["tradestatus"].eq("1")
        frame["is_st"] = frame["isST"].eq("1")
        frame["is_suspended_known"] = frame["tradestatus"].isin(["0", "1"])
        frame["is_st_known"] = frame["isST"].isin(["0", "1"])

        factors = (
            self.adjustment_factors(code, start_date, end_date)
            if adjustflag == "3" and include_adjustment_factors
            else pd.DataFrame()
        )
        if adjustflag != "3":
            # Back-adjusted prices (adjustflag=1) are stable for incremental
            # appends and already continuous across corporate actions.
            frame["adj_factor"] = 1.0
            frame["adjustment_factor_known"] = True
            frame["price_adjustment"] = "back_adjusted"
        elif not include_adjustment_factors:
            frame["adj_factor"] = 1.0
            frame["adjustment_factor_known"] = True
            frame["price_adjustment"] = "raw"
        elif factors.empty:
            frame["adj_factor"] = 1.0
            frame["adjustment_factor_known"] = False
            frame["price_adjustment"] = "raw_missing_factor"
        else:
            frame = pd.merge_asof(
                frame.sort_values("trade_date"),
                factors,
                on="trade_date",
                direction="backward",
            )
            frame["adj_factor"] = frame["adj_factor"].fillna(1.0)
            frame["adjustment_factor_known"] = True
            frame["price_adjustment"] = "raw_with_factor"
        frame["market_cap"] = np.nan
        frame["industry"] = "UNKNOWN"
        frame = derive_open_limit_flags(frame, adjusted_prices=adjustflag != "3")
        columns = [
            "trade_date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "preclose",
            "volume",
            "amount",
            "adj_factor",
            "turnover_rate",
            "pe_ttm",
            "pb_mrq",
            "ps_ttm",
            "pcf_ncf_ttm",
            "market_cap",
            "industry",
            "is_st",
            "is_suspended",
            "is_limit_up",
            "is_limit_down",
            "is_st_known",
            "is_suspended_known",
            "limit_status_known",
            "adjustment_factor_known",
            "up_limit",
            "down_limit",
        ]
        return normalize_daily_frame(frame[columns])

    def download_zz500(
        self,
        start_date: str,
        end_date: str,
        output_dir: str | Path,
    ) -> dict[str, Any]:
        """Download every unique constituent and save resumable per-symbol files."""
        output = Path(output_dir)
        daily_dir = output / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        calendar = self.trade_calendar(start_date, end_date)
        snapshots = self.monthly_zz500_snapshots(
            start_date, end_date, output / "membership_snapshots"
        )
        write_table(calendar, output / "trade_calendar.parquet")
        write_table(snapshots, output / "zz500_membership.parquet")
        symbols = sorted(snapshots["symbol"].unique())
        failures: list[dict[str, str]] = []
        completed = 0
        for number, symbol in enumerate(symbols, start=1):
            path = daily_dir / f"{symbol.replace('.', '_')}.parquet"
            if path.exists():
                completed += 1
                continue
            try:
                data = self.daily_history(
                    symbol_to_baostock(symbol), start_date, end_date
                )
                if not data.empty:
                    write_table(data, path)
                    completed += 1
            except Exception as exc:  # noqa: BLE001 - provider exception surface is broad
                failures.append({"symbol": symbol, "error": str(exc)})
            if number % 10 == 0 or number == len(symbols):
                print(
                    f"Daily histories: {number}/{len(symbols)}; "
                    f"completed={completed}, failures={len(failures)}",
                    flush=True,
                )
        manifest = {
            "provider": "baostock",
            "universe": "zz500_historical_monthly_snapshots",
            "start_date": start_date,
            "end_date": end_date,
            "symbols": len(symbols),
            "completed": completed,
            "failures": failures,
        }
        (output / "download_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest
