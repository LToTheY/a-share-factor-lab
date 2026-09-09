"""Data-quality and point-in-time completeness audit."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from quant_lab.data.schema import PRICE_COLUMNS, REQUIRED_COLUMNS


@dataclass(frozen=True)
class AuditIssue:
    severity: str
    code: str
    count: int
    message: str


@dataclass
class AuditResult:
    summary: dict[str, object]
    missing_ratio: dict[str, float]
    issues: list[AuditIssue]
    daily_coverage: pd.DataFrame

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "ERROR" for issue in self.issues)


def audit_daily_data(
    frame: pd.DataFrame,
    expected_trade_dates: Iterable[pd.Timestamp] | None = None,
    required_status_coverage: float = 0.99,
    expected_latest_symbols: int | None = None,
) -> AuditResult:
    """Audit research data without silently repairing it."""
    issues: list[AuditIssue] = []
    missing_columns = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing_columns:
        issues.append(
            AuditIssue(
                "ERROR", "MISSING_COLUMNS", len(missing_columns), str(missing_columns)
            )
        )
        return AuditResult(
            summary={"rows": len(frame), "symbols": 0},
            missing_ratio={},
            issues=issues,
            daily_coverage=pd.DataFrame(),
        )

    work = frame.copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    duplicate_count = int(work.duplicated(["trade_date", "symbol"]).sum())
    if duplicate_count:
        issues.append(
            AuditIssue(
                "ERROR", "DUPLICATE_KEYS", duplicate_count, "date-symbol主键重复"
            )
        )
    invalid_dates = int(work["trade_date"].isna().sum())
    if invalid_dates:
        issues.append(
            AuditIssue("ERROR", "INVALID_DATES", invalid_dates, "交易日无法解析")
        )

    invalid_ohlc = (
        (work[PRICE_COLUMNS] <= 0).any(axis=1)
        | (work["high"] < work[["open", "close", "low"]].max(axis=1))
        | (work["low"] > work[["open", "close", "high"]].min(axis=1))
    )
    if invalid_ohlc.any():
        issues.append(
            AuditIssue(
                "ERROR", "INVALID_OHLC", int(invalid_ohlc.sum()), "OHLC关系或价格非法"
            )
        )
    negative_activity = (work[["volume", "amount"]] < 0).any(axis=1)
    if negative_activity.any():
        issues.append(
            AuditIssue(
                "ERROR",
                "NEGATIVE_ACTIVITY",
                int(negative_activity.sum()),
                "成交量/额为负",
            )
        )

    if frame.attrs.get("provider") == "baostock":
        dual_price_columns = {"open", "close", "adj_open", "adj_close"}
        missing_dual = sorted(dual_price_columns - set(work.columns))
        if missing_dual:
            issues.append(
                AuditIssue(
                    "ERROR",
                    "RAW_ADJUSTED_PRICE_MISSING",
                    len(missing_dual),
                    f"原始成交价和复权研究价没有分离：{missing_dual}",
                )
            )
        elif (work[["adj_open", "adj_close"]] <= 0).any().any():
            issues.append(
                AuditIssue("ERROR", "INVALID_ADJUSTED_PRICE", 1, "复权价格存在非正数")
            )

    work = work.sort_values(["symbol", "trade_date"])
    adjusted_close = work["close"] * work.get("adj_factor", 1.0)
    returns = adjusted_close.groupby(work["symbol"], sort=False).pct_change()
    suspicious_jumps = returns.abs() > 0.25
    if suspicious_jumps.any():
        issues.append(
            AuditIssue(
                "WARNING",
                "LARGE_ADJUSTED_JUMPS",
                int(suspicious_jumps.sum()),
                "复权日收益绝对值超过25%，需抽样检查公司行动或数据错误",
            )
        )

    status_coverage = {}
    for column in ["is_st_known", "is_suspended_known", "limit_status_known"]:
        coverage = float(work[column].mean()) if column in work else 0.0
        status_coverage[column] = coverage
        if coverage < required_status_coverage:
            issues.append(
                AuditIssue(
                    "ERROR",
                    f"LOW_{column.upper()}",
                    int((1.0 - coverage) * len(work)),
                    f"{column}覆盖率{coverage:.2%}，不能声称已完整处理历史交易状态",
                )
            )

    missing_ratio = {
        column: float(value)
        for column, value in work.isna().mean().sort_values(ascending=False).items()
    }
    important_missing = sum(
        missing_ratio.get(column, 1.0) > 0.01
        for column in ["adj_factor", "market_cap", "industry"]
    )
    if important_missing:
        issues.append(
            AuditIssue(
                "WARNING",
                "IMPORTANT_MISSING",
                important_missing,
                "复权、市值或行业字段缺失超过1%",
            )
        )

    if "in_index" in work:
        index_counts = work.groupby("trade_date")["in_index"].sum()
        populated = index_counts[index_counts > 0]
        if populated.empty or populated.median() < 10:
            issues.append(
                AuditIssue(
                    "ERROR",
                    "INDEX_MEMBERSHIP_EMPTY",
                    int((index_counts == 0).sum()),
                    "历史指数成分字段存在，但有效成分数量异常",
                )
            )

    daily = (
        work.groupby("trade_date")
        .agg(
            symbols=("symbol", "nunique"),
            amount_missing=("amount", lambda values: float(values.isna().mean())),
            suspended=("is_suspended", "sum")
            if "is_suspended" in work
            else ("symbol", "size"),
        )
        .reset_index()
    )
    missing_expected = 0
    recent_symbol_gaps = 0
    if expected_trade_dates is not None:
        expected = {
            pd.Timestamp(value) for value in pd.to_datetime(list(expected_trade_dates))
        }
        observed = {
            pd.Timestamp(value) for value in work["trade_date"].dropna().unique()
        }
        missing_expected = len(expected - observed)
        if missing_expected:
            issues.append(
                AuditIssue(
                    "ERROR",
                    "MISSING_TRADE_DATES",
                    missing_expected,
                    "交易日历中存在整日缺失",
                )
            )
        recent_dates = sorted(expected)[-60:]
        if recent_dates:
            recent_set = set(recent_dates)
            latest_market_date = work["trade_date"].max()
            latest_members = set(
                work.loc[
                    (work["trade_date"] == latest_market_date)
                    & work.get("in_index", True),
                    "symbol",
                ]
            )
            for symbol in latest_members:
                symbol_rows = work[work["symbol"] == symbol]
                first_date = symbol_rows["trade_date"].min()
                required = {date for date in recent_set if date >= first_date}
                observed_symbol = set(symbol_rows["trade_date"])
                recent_symbol_gaps += len(required - observed_symbol)
            if recent_symbol_gaps:
                issues.append(
                    AuditIssue(
                        "ERROR",
                        "RECENT_SYMBOL_DATE_GAPS",
                        recent_symbol_gaps,
                        "最新成分股最近60个交易日存在内部行情缺口",
                    )
                )

    latest_date = work["trade_date"].max()
    latest = work[work["trade_date"] == latest_date]
    latest_symbols = int(
        latest.loc[latest["in_index"], "symbol"].nunique()
        if "in_index" in latest
        else latest["symbol"].nunique()
    )
    if expected_latest_symbols is not None and latest_symbols < expected_latest_symbols:
        issues.append(
            AuditIssue(
                "ERROR",
                "INCOMPLETE_LATEST_CROSS_SECTION",
                expected_latest_symbols - latest_symbols,
                f"最新交易日仅有{latest_symbols}/{expected_latest_symbols}只有效股票",
            )
        )

    summary: dict[str, object] = {
        "rows": len(work),
        "symbols": int(work["symbol"].nunique()),
        "start_date": str(work["trade_date"].min().date()),
        "end_date": str(work["trade_date"].max().date()),
        "trade_dates": int(work["trade_date"].nunique()),
        "latest_symbols": latest_symbols,
        "duplicate_keys": duplicate_count,
        "missing_expected_trade_dates": missing_expected,
        "recent_symbol_date_gaps": recent_symbol_gaps,
        "status_coverage": status_coverage,
        "audit_passed": not any(issue.severity == "ERROR" for issue in issues),
    }
    return AuditResult(summary, missing_ratio, issues, daily)


def write_audit_report(result: AuditResult, output_dir: str | Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": result.summary,
        "missing_ratio": result.missing_ratio,
        "issues": [asdict(issue) for issue in result.issues],
    }
    (output / "audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result.daily_coverage.to_csv(output / "daily_coverage.csv", index=False)
    pd.DataFrame([asdict(issue) for issue in result.issues]).to_csv(
        output / "audit_issues.csv", index=False, encoding="utf-8-sig"
    )
    lines = ["# 数据审计报告", "", f"- 通过：{result.summary.get('audit_passed')}"]
    for key, value in result.summary.items():
        if key not in {"audit_passed", "status_coverage"}:
            lines.append(f"- {key}: {value}")
    lines.extend(["", "## 问题", ""])
    if result.issues:
        lines.extend(
            f"- **{issue.severity} {issue.code}** ({issue.count}): {issue.message}"
            for issue in result.issues
        )
    else:
        lines.append("- 未发现问题。")
    (output / "audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
