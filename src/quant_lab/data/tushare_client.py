"""Thin Tushare boundary. It intentionally does not leak provider fields inward."""

from __future__ import annotations

import calendar
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd

from quant_lab.data.schema import normalize_daily_frame
from quant_lab.data.storage import write_table


class TushareDownloader:
    """Download provider-native tables with explicit caching.

    The first production milestone uses daily and adj_factor. Daily_basic and
    point-in-time status tables can be joined later without changing the schema.
    """

    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.getenv("TUSHARE_TOKEN")
        if not self.token:
            raise ValueError("Set TUSHARE_TOKEN in the environment or .env file")
        try:
            import tushare as ts
        except ImportError as exc:
            raise RuntimeError("Install with: pip install tushare") from exc
        self._api = ts.pro_api(self.token)
        self.warnings: list[str] = []

    def _optional_call(
        self, endpoint: str, **kwargs: object
    ) -> tuple[pd.DataFrame, bool]:
        """Call a status endpoint and preserve permission failure as metadata."""
        try:
            result = getattr(self._api, endpoint)(**kwargs)
            return result, True
        except Exception as exc:  # noqa: BLE001 - provider returns varied exceptions
            self.warnings.append(f"{endpoint}: {type(exc).__name__}: {exc}")
            return pd.DataFrame(), False

    def download_security_master(self) -> pd.DataFrame:
        """Download listed/delisted securities; industry is current, not PIT."""
        fields = "ts_code,symbol,name,area,industry,list_date,delist_date,list_status"
        tables = [
            self._api.stock_basic(exchange="", list_status=status, fields=fields)
            for status in ["L", "D", "P"]
        ]
        master = pd.concat(
            [table for table in tables if not table.empty], ignore_index=True
        )
        master = master.drop_duplicates("ts_code", keep="last")
        master["list_date"] = pd.to_datetime(
            master["list_date"], format="%Y%m%d", errors="coerce"
        )
        return master

    def download_trade_date(
        self,
        trade_date: str,
        security_master: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Download all A-share daily bars for one YYYYMMDD trade date."""
        daily = self._api.daily(trade_date=trade_date)
        adj = self._api.adj_factor(trade_date=trade_date)
        daily_basic = self._api.daily_basic(
            trade_date=trade_date,
            fields="ts_code,trade_date,turnover_rate,total_mv,circ_mv",
        )
        if daily.empty:
            return daily
        limits, limit_known = self._optional_call("stk_limit", trade_date=trade_date)
        suspended, suspended_known = self._optional_call(
            "suspend_d", trade_date=trade_date, suspend_type="S"
        )
        st_stocks, st_known = self._optional_call("stock_st", trade_date=trade_date)
        if suspended_known and not suspended.empty:
            missing_codes = sorted(
                set(suspended["ts_code"].astype(str)) - set(daily["ts_code"])
            )
            if missing_codes:
                placeholders = pd.DataFrame(
                    {"ts_code": missing_codes, "trade_date": trade_date}
                )
                daily = pd.concat([daily, placeholders], ignore_index=True, sort=False)
        frame = daily.merge(adj[["ts_code", "adj_factor"]], on="ts_code", how="left")
        if not daily_basic.empty:
            frame = frame.merge(
                daily_basic[["ts_code", "turnover_rate", "total_mv", "circ_mv"]],
                on="ts_code",
                how="left",
            )
            # Tushare market values are reported in 10,000 CNY.
            frame["market_cap"] = frame["total_mv"] * 10_000.0
            frame["float_market_cap"] = frame["circ_mv"] * 10_000.0
        if security_master is not None and not security_master.empty:
            frame = frame.merge(
                security_master[["ts_code", "name", "industry", "list_date"]],
                on="ts_code",
                how="left",
            )
            # This name is not a reliable historical ST flag. It is retained for
            # inspection but never converted to is_st automatically.
        if limit_known:
            frame = frame.merge(
                limits[["ts_code", "up_limit", "down_limit"]],
                on="ts_code",
                how="left",
            )
            tolerance = 1e-6
            frame["is_limit_up"] = frame["open"] >= frame["up_limit"] * (1 - tolerance)
            frame["is_limit_down"] = frame["open"] <= frame["down_limit"] * (
                1 + tolerance
            )
        else:
            frame["is_limit_up"] = False
            frame["is_limit_down"] = False
        frame["limit_status_known"] = limit_known

        suspended_codes = set(
            suspended.get("ts_code", pd.Series(dtype=str)).astype(str)
        )
        frame["is_suspended"] = frame["ts_code"].isin(suspended_codes)
        frame["is_suspended_known"] = suspended_known
        st_codes = set(st_stocks.get("ts_code", pd.Series(dtype=str)).astype(str))
        frame["is_st"] = frame["ts_code"].isin(st_codes)
        frame["is_st_known"] = st_known
        frame = frame.rename(columns={"trade_date": "trade_date", "ts_code": "symbol"})
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
        # Tushare volume is lots and amount is thousand CNY; normalize to shares/CNY.
        frame["volume"] = frame["vol"] * 100.0
        frame["amount"] = frame["amount"] * 1000.0
        keep = [
            "trade_date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "adj_factor",
        ]
        optional = [
            column
            for column in [
                "turnover_rate",
                "market_cap",
                "float_market_cap",
                "name",
                "industry",
                "list_date",
                "up_limit",
                "down_limit",
                "is_limit_up",
                "is_limit_down",
                "is_suspended",
                "is_st",
                "limit_status_known",
                "is_suspended_known",
                "is_st_known",
            ]
            if column in frame
        ]
        return normalize_daily_frame(frame[keep + optional])

    def download_range(
        self,
        start_date: str,
        end_date: str,
        output_dir: str | Path,
    ) -> list[Path]:
        """Cache one file per open date, making interrupted downloads resumable."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        master_path = output.parent / "security_master.parquet"
        if master_path.exists():
            security_master = pd.read_parquet(master_path)
        else:
            security_master = self.download_security_master()
            write_table(security_master, master_path)
        calendar = self._api.trade_cal(
            exchange="SSE", start_date=start_date, end_date=end_date, is_open="1"
        )
        calendar_output = calendar.copy()
        calendar_output["trade_date"] = pd.to_datetime(
            calendar_output["cal_date"], format="%Y%m%d"
        )
        write_table(
            calendar_output[["trade_date", "pretrade_date"]],
            output.parent / "trade_calendar.parquet",
        )
        paths = []
        for trade_date in sorted(calendar["cal_date"].astype(str)):
            path = output / f"daily_{trade_date}.parquet"
            if not path.exists():
                write_table(self.download_trade_date(trade_date, security_master), path)
            paths.append(path)
        (output.parent / "download_manifest.json").write_text(
            json.dumps(
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "partitions": len(paths),
                    "warnings": self.warnings,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return paths

    def download_index_weights(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
        output_path: str | Path,
    ) -> Path:
        """Download monthly constituent snapshots as recommended by Tushare."""
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        months = pd.period_range(start, end, freq="M")
        frames = []
        for month in months:
            first = date(month.year, month.month, 1)
            last = date(
                month.year, month.month, calendar.monthrange(month.year, month.month)[1]
            )
            current = self._api.index_weight(
                index_code=index_code,
                start_date=max(first, start.date()).strftime("%Y%m%d"),
                end_date=min(last, end.date()).strftime("%Y%m%d"),
            )
            if not current.empty:
                frames.append(current)
        if not frames:
            raise RuntimeError(f"No index weights returned for {index_code}")
        weights = pd.concat(frames, ignore_index=True).drop_duplicates(
            ["trade_date", "con_code"], keep="last"
        )
        weights = weights.rename(columns={"con_code": "symbol"})
        weights["trade_date"] = pd.to_datetime(weights["trade_date"], format="%Y%m%d")
        return write_table(
            weights[["trade_date", "symbol", "weight", "index_code"]], output_path
        )
