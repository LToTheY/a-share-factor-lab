"""Audit local CSMAR distributions without changing the research price ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://cn.ceibs.edu/pdf/library/40030.pdf"


def parse_date(values):
    text = values.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    compact = text.str.fullmatch(r"\d{8}")
    result = pd.to_datetime(text.where(~compact), errors="coerce")
    result.loc[compact] = pd.to_datetime(text.loc[compact], format="%Y%m%d", errors="coerce")
    return result.dt.normalize()


def normalize_events(raw: pd.DataFrame) -> pd.DataFrame:
    required = {"stkcd", "disttyp", "exdistdt", "annodt", "paydt", "amount", "roprc"}
    if not required.issubset(raw):
        raise ValueError(f"Missing CSMAR fields: {sorted(required - set(raw))}")
    result = pd.DataFrame(index=raw.index)
    code = raw.stkcd.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    result["symbol"] = code + np.where(code.str.startswith("6"), ".SH", ".SZ")
    result["kind"] = raw.disttyp.astype(str).str.strip().str.upper()
    result["ex_date"] = parse_date(raw.exdistdt)
    result["announcement_date"] = parse_date(raw.annodt)
    result["payment_date"] = parse_date(raw.paydt)
    result["amount_per_share"] = pd.to_numeric(raw.amount, errors="coerce")
    result["rights_price"] = pd.to_numeric(raw.roprc, errors="coerce")
    reasons = pd.Series("", index=raw.index)
    checks = {
        "unsupported_symbol": ~code.str.fullmatch(r"[036]\d{5}"),
        "unsupported_kind": ~result.kind.isin(["CA", "SD"]),
        "invalid_ex_date": result.ex_date.isna(),
        "unknown_announcement": result.announcement_date.isna(),
        "announcement_after_ex_date": result.announcement_date > result.ex_date,
        "unknown_payment_date": result.payment_date.isna(),
        "payment_before_ex_date": result.payment_date < result.ex_date,
        "invalid_amount": ~np.isfinite(result.amount_per_share) | (result.amount_per_share < 0),
        "duplicate_event": result.duplicated(["symbol", "ex_date", "kind"], keep=False),
    }
    for label, mask in checks.items():
        reasons.loc[mask] += label + ";"
    result["issues"] = reasons.str.rstrip(";")
    result["supported_for_gross_ledger"] = reasons.eq("")
    return result.sort_values(["ex_date", "symbol", "kind"]).reset_index(drop=True)


def reconcile_prices(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Compare only adjacent observed global sessions; no gap-spanning inference."""
    market = prices.sort_values(["symbol", "trade_date"]).copy()
    market["trade_date"] = pd.to_datetime(market.trade_date)
    if market.duplicated(["symbol", "trade_date"]).any():
        raise ValueError("Duplicate market date-symbol keys")
    dates = pd.Series(range(market.trade_date.nunique()), index=sorted(market.trade_date.unique()))
    market["session"] = market.trade_date.map(dates)
    grouped = market.groupby("symbol", sort=False)
    market["previous_close"] = grouped.close.shift()
    market["adjacent_session"] = market.session.sub(grouped.session.shift()).eq(1)
    market.loc[~market.adjacent_session, "previous_close"] = np.nan
    groups = events.groupby(["symbol", "ex_date"], dropna=False)
    joined = groups.agg(event_count=("kind", "size"),
                        all_events_supported=("supported_for_gross_ledger", "all")).reset_index()
    for kind, field in [("CA", "cash_per_share"), ("SD", "bonus_ratio")]:
        values = events.loc[events.kind.eq(kind)].groupby(["symbol", "ex_date"]).amount_per_share.sum(min_count=1)
        joined = joined.merge(values.rename(field), on=["symbol", "ex_date"], how="left")
        joined[field] = joined[field].fillna(0.)
    joined = joined.merge(market[["symbol", "trade_date", "previous_close", "preclose", "adjacent_session"]],
                          left_on=["symbol", "ex_date"], right_on=["symbol", "trade_date"], how="left", validate="one_to_one")
    eligible = (joined.all_events_supported & joined.previous_close.gt(0) & joined.preclose.gt(0)
                & joined.cash_per_share.lt(joined.previous_close))
    joined["expected_preclose"] = ((joined.previous_close - joined.cash_per_share) / (1 + joined.bonus_ratio)).where(eligible)
    joined["absolute_error"] = (joined.expected_preclose - joined.preclose).abs()
    joined["status"] = np.select([(~eligible).fillna(True).to_numpy(dtype=bool), joined.absolute_error.le(.0151).fillna(False).to_numpy(dtype=bool)],
                                  ["NOT_COMPARABLE", "MATCH_WITHIN_TICK_TOLERANCE"], default="REVIEW_DIFFERENCE")
    return joined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reports/generated/corporate_action_audit")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to((ROOT / "reports/generated").resolve()):
        raise ValueError("Output must stay inside ignored reports/generated")
    files = sorted((ROOT / "data/raw/csmar/bulk/csmar_trade/trd_cptl").glob("*.parquet"))
    if not files:
        raise ValueError("No local CSMAR distribution files; this audit does not download data")
    raw = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    events = normalize_events(raw)
    prices = pd.read_parquet(ROOT / "data/processed/market_daily.parquet", columns=["trade_date", "symbol", "close", "preclose"])
    comparison = reconcile_prices(events, prices)
    output.mkdir(parents=True, exist_ok=True)
    events.to_parquet(output / "normalized_events.parquet", index=False)
    comparison.to_csv(output / "reference_price_checks.csv", index=False, encoding="utf-8-sig")
    summary = {
        "source_manual": SOURCE, "manual_pdf_pages": [22, 41],
        "rows": len(events), "kinds": events.kind.value_counts().to_dict(),
        "first_ex_date": str(events.ex_date.min().date()), "last_ex_date": str(events.ex_date.max().date()),
        "unsupported_or_invalid_rows": int((~events.supported_for_gross_ledger).sum()),
        "reference_checks": comparison.status.value_counts().to_dict(),
        "input_prices_last_date": str(pd.Timestamp(prices.trade_date.max()).date()),
        "ledger_changed": False,
        "limitations": ["字段说明不是完整性保证；原始数据覆盖到期之后的分红送转仍未知。",
                        "匹配仅核对CA现金与SD新增股份比例；不模拟红利税、派息延迟、零碎股与权益登记。",
                        "相邻交易日昨收相符不等于真实账户收益精确；停牌与其他公司行为须另核对。"],
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
