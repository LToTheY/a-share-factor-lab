"""Fixed-configuration chronological validation and one-variable comparisons."""

from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from quant_lab.evaluation.diagnostics import (
    add_execution_returns,
    information_coefficient,
)
from quant_lab.strategy.sandbox import build_composite_signal, run_strategy


def walk_forward_strategy(scores, market, spec, config, output: Path, *, train_years=5,
                          validation_years=1, test_years=1, horizon=5, embargo=5,
                          progress=lambda message: None) -> dict:
    """Keep the user's weights/rules fixed. No selection on validation or test returns."""
    if min(train_years, validation_years, test_years, horizon) < 1:
        raise ValueError("训练、验证、测试窗口与预测期必须为正")
    embargo = max(embargo, horizon + 1)
    output.mkdir(parents=True, exist_ok=True)
    dates = pd.DatetimeIndex(sorted(scores.trade_date.unique()))
    first = dates[0]
    test_start = first + pd.DateOffset(years=train_years + validation_years)
    if test_start + pd.DateOffset(years=test_years) > dates[-1] + pd.Timedelta(days=7):
        return {"status": "insufficient_history", "message": f"窗口不足：需要至少 {train_years}+{validation_years}+{test_years} 年完整区间；当前 {first.date()} 至 {dates[-1].date()}", "folds": []}
    signal = build_composite_signal(scores, spec)
    label = f"next_open_return_{horizon}d"
    label_market = market
    if "adj_open" not in label_market:
        label_market = market.assign(adj_open=market.open * market.adj_close / market.close.where(market.close.gt(0)))
    labels = add_execution_returns(label_market, horizon)[["trade_date", "symbol", label]]
    diagnostic = signal.merge(labels, on=["trade_date", "symbol"], validate="one_to_one")
    rows = []
    while test_start + pd.DateOffset(years=test_years) <= dates[-1] + pd.Timedelta(days=7):
        validation_start = test_start - pd.DateOffset(years=validation_years)
        train_start = validation_start - pd.DateOffset(years=train_years)
        test_end = test_start + pd.DateOffset(years=test_years)

        def past_window(start, end):
            selected_dates = dates[(dates >= start) & (dates < end)]
            return diagnostic[diagnostic.trade_date.isin(selected_dates[:-embargo])] if len(selected_dates) > embargo else diagnostic.iloc[:0]

        train = past_window(train_start, validation_start)
        validation = past_window(validation_start, test_start)
        test_mask = (scores.trade_date >= test_start) & (scores.trade_date < test_end)
        test_market = market[(market.trade_date >= test_start) & (market.trade_date < test_end)]
        if train.empty or validation.empty or not test_mask.any():
            break
        progress(f"样本外测试 {test_start.date()} 至 {test_end.date()}")
        result = run_strategy(scores[test_mask], test_market, spec, config, progress=progress)
        fold = output / f"fold_{len(rows)+1}"
        fold.mkdir(exist_ok=True)
        for key, frame in {"equity": result.backtest.equity, "trades": result.backtest.trades,
                           "positions": result.backtest.positions, "targets": result.targets,
                           "execution_issues": result.backtest.execution_issues,
                           "selection_audit": result.selection_audit}.items():
            frame.to_parquet(fold / f"{key}.parquet", index=False)
        rows.append({"train_start": str(train.trade_date.min().date()), "train_end": str(train.trade_date.max().date()),
                     "validation_start": str(validation.trade_date.min().date()), "validation_end": str(validation.trade_date.max().date()),
                     "test_start": str(test_market.trade_date.min().date()), "test_end": str(test_market.trade_date.max().date()),
                     "train_ic": float(information_coefficient(train, "factor_processed", label).mean()),
                     "validation_ic": float(information_coefficient(validation, "factor_processed", label).mean()),
                     **result.metrics})
        test_start = test_end
    pd.DataFrame(rows).to_csv(output / "folds.csv", index=False)
    return {"status": "ready" if rows else "insufficient_history", "folds": rows,
            "embargo": embargo, "strategy": asdict(spec), "backtest": asdict(config),
            "diagnostic_label": label,
            "method": "参数及因子权重固定；训练/验证仅诊断；每折独立从初始资金开始，不自动选优。观察过测试收益后再改参数不构成新样本外证据。"}


def robustness(scores, market, spec, config, output: Path, progress=lambda message: None) -> list[dict]:
    """Change exactly one dimension; preserve every full result, including failures."""
    import json

    candidates = [("baseline", spec, config)]
    for count in (5, 10, 20):
        # Exit rank stays fixed. Invalid combinations are explicitly reported below.
        if spec.exit_rank >= count:
            candidates.append((f"holdings_{count}", replace(spec, top_n=count), config))
    for frequency in ("W-FRI", "M"):
        candidates.append((f"frequency_{frequency}", replace(spec, rebalance_frequency=frequency), config))
    candidates.append(("double_cost", spec, replace(config, commission_rate=config.commission_rate*2,
        minimum_commission=config.minimum_commission*2, transfer_fee_rate=config.transfer_fee_rate*2,
        stamp_duty_rate=config.stamp_duty_rate*2, historical_stamp_duty_rate=config.historical_stamp_duty_rate*2,
        slippage_bps=config.slippage_bps*2)))
    rows = [{"case": f"holdings_{n}", "status": "invalid", "reason": "保持退出排名不变时 exit_rank < top_n，请提高基线退出排名"}
            for n in (5, 10, 20) if spec.exit_rank < n]
    output.mkdir(parents=True, exist_ok=True)
    for name, candidate, costs in candidates:
        progress(f"单因素对照：{name}")
        folder = output / name
        folder.mkdir(exist_ok=True)
        settings = {"strategy": asdict(candidate), "backtest": asdict(costs)}
        (folder / "config.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
        try:
            result = run_strategy(scores, market, candidate, costs, progress=progress)
            for key, frame in {"equity": result.backtest.equity, "trades": result.backtest.trades,
                               "positions": result.backtest.positions, "targets": result.targets,
                               "execution_issues": result.backtest.execution_issues,
                               "selection_audit": result.selection_audit}.items():
                frame.to_parquet(folder / f"{key}.parquet", index=False)
            rows.append({"case": name, "status": "ready", **result.metrics, **result.diagnostics})
        except ValueError as exc:
            rows.append({"case": name, "status": "failed", "reason": str(exc)})
    pd.DataFrame(rows).to_csv(output / "comparison.csv", index=False)
    return rows
