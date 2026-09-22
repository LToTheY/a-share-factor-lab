"""Leakage-aware walk-forward comparison of factor models on real data."""

from __future__ import annotations

import json
import platform
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant_lab.backtest.engine import BacktestConfig, run_backtest
from quant_lab.backtest.metrics import (
    equal_weight_benchmark,
    performance_metrics,
    turnover_from_trades,
)
from quant_lab.data.schema import require_columns
from quant_lab.evaluation.diagnostics import summarize_ic
from quant_lab.models.dataset import cross_sectional_rank_label
from quant_lab.models.lightgbm_model import fit_lightgbm_model, save_lightgbm_model
from quant_lab.models.mlp import MLPRegressor
from quant_lab.models.ridge import RidgeRegressor, prediction_rank_ic
from quant_lab.models.settings import MLSettings
from quant_lab.portfolio.weights import buffered_top_n_weights
from quant_lab.reporting import markdown_table
from quant_lab.research.settings import ResearchSettings


def _drop_last_dates(frame: pd.DataFrame, count: int) -> pd.DataFrame:
    if count <= 0 or frame.empty:
        return frame
    dates = pd.DatetimeIndex(sorted(frame["trade_date"].unique()))
    if len(dates) <= count:
        return frame.iloc[0:0]
    return frame[frame["trade_date"].isin(dates[:-count])].copy()


def _mean_prediction_ic(frame: pd.DataFrame, prediction_col: str) -> float:
    values = prediction_rank_ic(
        frame,
        prediction_col=prediction_col,
        label_col="label",
    ).dropna()
    return float(values.mean()) if not values.empty else float("nan")


def _prepare_model_frame(scores: pd.DataFrame, settings: MLSettings) -> pd.DataFrame:
    required = [
        "trade_date",
        "symbol",
        "in_universe",
        settings.label_col,
        *settings.feature_names,
    ]
    require_columns(scores, required)
    work = scores[required].copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"])
    feature_count = work[list(settings.feature_names)].notna().sum(axis=1)
    work = work[
        work["in_universe"].fillna(False)
        & (feature_count >= settings.minimum_valid_features)
    ].copy()
    work = cross_sectional_rank_label(work, settings.label_col, output_col="label")
    return work.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def run_ml_study(
    scores: pd.DataFrame,
    market: pd.DataFrame,
    settings: MLSettings,
    research_settings: ResearchSettings,
    output_dir: str | Path,
    *,
    enabled_models: tuple[str, ...] | None = None,
    next_trading_date: str | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Tune on past validation years and predict each following test year."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    model_dir = output / "fold_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    models = enabled_models or settings.enabled_models
    unknown = sorted(set(models) - {"ridge", "lightgbm", "mlp"})
    if unknown:
        raise ValueError(f"Unsupported ML models: {', '.join(unknown)}")

    work = _prepare_model_frame(scores, settings)
    first_year = pd.Timestamp(
        research_settings.research_start_date or work["trade_date"].min()
    ).year
    last_year = int(work["trade_date"].dt.year.max())
    first_test_year = first_year + settings.train_years + settings.validation_years
    prediction_frames: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    importance_rows: list[dict[str, Any]] = []

    for test_year in range(first_test_year, last_year + 1, settings.step_years):
        test_end_year = min(test_year + settings.test_years, last_year + 1)
        validation_start = test_year - settings.validation_years
        train_start = validation_start - settings.train_years
        train = work[
            (work["trade_date"].dt.year >= train_start)
            & (work["trade_date"].dt.year < validation_start)
        ]
        validation = work[
            (work["trade_date"].dt.year >= validation_start)
            & (work["trade_date"].dt.year < test_year)
        ]
        test = work[
            (work["trade_date"].dt.year >= test_year)
            & (work["trade_date"].dt.year < test_end_year)
        ].copy()
        train = _drop_last_dates(train, settings.embargo_trading_days)
        validation = _drop_last_dates(validation, settings.embargo_trading_days)
        train_fit = train.dropna(subset=["label"])
        validation_fit = validation.dropna(subset=["label"])
        if train_fit.empty or validation_fit.empty or test.empty:
            continue

        features = list(settings.feature_names)
        fold_prediction = test[["trade_date", "symbol", "label"]].copy()
        fold_prediction["walk_forward_fold"] = test_year
        fold_prediction["equal_weight_prediction"] = test[features].mean(axis=1)
        fold_row: dict[str, Any] = {
            "test_year": test_year,
            "test_end_year": test_end_year - 1,
            "train_start_year": train_start,
            "validation_start_year": validation_start,
            "train_rows": len(train_fit),
            "validation_rows": len(validation_fit),
            "test_rows": len(test),
            "equal_weight_test_ic": _mean_prediction_ic(
                fold_prediction, "equal_weight_prediction"
            ),
        }

        if "ridge" in models:
            alpha_scores: list[tuple[float, float]] = []
            ridge_parameters = settings.model_parameters.get("ridge", {})
            for raw_alpha in ridge_parameters.get("alphas", [1.0]):
                alpha = float(raw_alpha)
                candidate = RidgeRegressor(alpha=alpha).fit(
                    train_fit[features], train_fit["label"]
                )
                validation_scored = validation_fit[
                    ["trade_date", "symbol", "label"]
                ].copy()
                validation_scored["prediction"] = candidate.predict(
                    validation_fit[features]
                )
                alpha_scores.append(
                    (alpha, _mean_prediction_ic(validation_scored, "prediction"))
                )
            finite_scores = [item for item in alpha_scores if np.isfinite(item[1])]
            if not finite_scores:
                raise RuntimeError(f"Ridge validation IC is unavailable for {test_year}")
            best_alpha, best_validation_ic = max(
                finite_scores, key=lambda item: (item[1], -item[0])
            )
            ridge_training = pd.concat([train_fit, validation_fit], ignore_index=True)
            ridge = RidgeRegressor(alpha=best_alpha).fit(
                ridge_training[features], ridge_training["label"]
            )
            fold_prediction["ridge_prediction"] = ridge.predict(test[features])
            fold_row["ridge_alpha"] = best_alpha
            fold_row["ridge_validation_ic"] = best_validation_ic
            fold_row["ridge_test_ic"] = _mean_prediction_ic(
                fold_prediction, "ridge_prediction"
            )
            importance_rows.extend(
                {
                    "test_year": test_year,
                    "model": "ridge",
                    "feature": feature,
                    "importance": float(coefficient),
                }
                for feature, coefficient in zip(features, ridge.coefficients_)
            )
            ridge_state = {
                "model": "ridge",
                "test_year": test_year,
                "alpha": best_alpha,
                "features": features,
                "intercept": ridge.intercept_,
                "coefficients": ridge.coefficients_.tolist(),
                "medians": ridge.medians_.tolist(),
                "means": ridge.means_.tolist(),
                "scales": ridge.scales_.tolist(),
            }
            (model_dir / f"ridge_{test_year}.json").write_text(
                json.dumps(ridge_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        if "lightgbm" in models:
            parameters = dict(settings.model_parameters.get("lightgbm", {}))
            early_stopping_rounds = int(parameters.pop("early_stopping_rounds", 50))
            lightgbm = fit_lightgbm_model(
                train_fit[features],
                train_fit["label"],
                validation_fit[features],
                validation_fit["label"],
                early_stopping_rounds=early_stopping_rounds,
                **parameters,
            )
            fold_prediction["lightgbm_prediction"] = lightgbm.predict(test[features])
            fold_row["lightgbm_best_iteration"] = int(lightgbm.best_iteration_)
            fold_row["lightgbm_test_ic"] = _mean_prediction_ic(
                fold_prediction, "lightgbm_prediction"
            )
            importance_rows.extend(
                {
                    "test_year": test_year,
                    "model": "lightgbm",
                    "feature": feature,
                    "importance": float(importance),
                }
                for feature, importance in zip(features, lightgbm.feature_importances_)
            )
            save_lightgbm_model(
                lightgbm,
                model_dir / f"lightgbm_{test_year}.txt",
                num_iteration=lightgbm.best_iteration_,
            )

        if "mlp" in models:
            parameters = dict(settings.model_parameters.get("mlp", {}))
            mlp = MLPRegressor(**parameters).fit(
                train_fit[features],
                train_fit["label"],
                validation_data=(validation_fit[features], validation_fit["label"]),
            )
            fold_prediction["mlp_prediction"] = mlp.predict(test[features])
            fold_row["mlp_best_epoch"] = mlp.best_epoch_
            fold_row["mlp_test_ic"] = _mean_prediction_ic(
                fold_prediction, "mlp_prediction"
            )
            if mlp.training_history_:
                history = pd.DataFrame(mlp.training_history_)
                history.insert(0, "test_year", test_year)
                history.to_csv(
                    output / f"mlp_training_{test_year}.csv", index=False
                )
            torch = mlp._torch()
            torch.save(
                {
                    "model": "mlp",
                    "test_year": test_year,
                    "features": features,
                    "best_epoch": mlp.best_epoch_,
                    "state_dict": mlp.model_.state_dict(),
                    "medians": mlp.medians_,
                    "means": mlp.means_,
                    "scales": mlp.scales_,
                    "parameters": parameters,
                },
                model_dir / f"mlp_{test_year}.pt",
            )

        prediction_frames.append(fold_prediction)
        fold_rows.append(fold_row)

    if not prediction_frames:
        raise RuntimeError("Insufficient history for the configured ML walk-forward")

    predictions = pd.concat(prediction_frames, ignore_index=True).sort_values(
        ["trade_date", "symbol"]
    )
    folds = pd.DataFrame(fold_rows)
    importance = pd.DataFrame(importance_rows)
    if not importance.empty:
        absolute_sum = importance.groupby(["test_year", "model"])[
            "importance"
        ].transform(lambda values: values.abs().sum())
        importance["normalized_importance"] = importance["importance"].div(
            absolute_sum.replace(0, np.nan)
        )
        feature_stability = (
            importance.groupby(["model", "feature"], as_index=False)
            .agg(
                mean_normalized_importance=("normalized_importance", "mean"),
                importance_std=("normalized_importance", "std"),
                positive_share=("importance", lambda values: float((values > 0).mean())),
                folds=("test_year", "nunique"),
            )
        )
        feature_stability["_absolute_importance"] = feature_stability[
            "mean_normalized_importance"
        ].abs()
        feature_stability = feature_stability.sort_values(
            ["model", "_absolute_importance"], ascending=[True, False]
        ).drop(columns="_absolute_importance")
    else:
        feature_stability = pd.DataFrame(
            columns=[
                "model",
                "feature",
                "mean_normalized_importance",
                "importance_std",
                "positive_share",
                "folds",
            ]
        )
    predictions.to_parquet(output / "oos_predictions.parquet", index=False)
    folds.to_csv(output / "fold_summary.csv", index=False, encoding="utf-8-sig")
    importance.to_csv(
        output / "feature_importance.csv", index=False, encoding="utf-8-sig"
    )
    feature_stability.to_csv(
        output / "feature_stability.csv", index=False, encoding="utf-8-sig"
    )

    model_names = ["equal_weight", *models]
    backtest_config = BacktestConfig(**research_settings.backtest)
    start = pd.Timestamp(predictions["trade_date"].min())
    oos_market = market[pd.to_datetime(market["trade_date"]) >= start].copy()
    benchmark_eligibility = "in_index" if "in_index" in market else "in_universe"
    benchmark_price = "adj_close" if "adj_close" in market else "close"
    benchmark = equal_weight_benchmark(
        market,
        backtest_config.initial_cash,
        eligibility_col=benchmark_eligibility,
        price_col=benchmark_price,
        start_date=start,
    )
    benchmark_metrics = performance_metrics(benchmark)
    benchmark.to_csv(output / "benchmark_equity.csv", index=False)
    no_cost_config = replace(
        backtest_config,
        commission_rate=0.0,
        stamp_duty_rate=0.0,
        historical_stamp_duty_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_bps=0.0,
        minimum_commission=0.0,
    )

    summaries: dict[str, Any] = {}
    report_rows: list[dict[str, Any]] = []
    for model_name in model_names:
        prediction_col = f"{model_name}_prediction"
        ic = prediction_rank_ic(
            predictions,
            prediction_col=prediction_col,
            label_col="label",
        )
        ic_summary = summarize_ic(
            ic, periods_per_year=252 / research_settings.forward_periods
        )
        targets = buffered_top_n_weights(
            predictions,
            signal_col=prediction_col,
            top_n=research_settings.top_n,
            exit_rank=research_settings.exit_rank,
            frequency=research_settings.rebalance_frequency,
            max_weight=research_settings.max_weight,
            next_trading_date=next_trading_date,
        )
        result = run_backtest(oos_market, targets, backtest_config)
        portfolio = performance_metrics(result.equity)
        portfolio["turnover"] = turnover_from_trades(result.trades, result.equity)
        portfolio["fees"] = (
            float(result.trades["fee"].sum()) if not result.trades.empty else 0.0
        )
        portfolio["tax"] = (
            float(result.trades["tax"].sum()) if not result.trades.empty else 0.0
        )
        portfolio["trades"] = len(result.trades)
        no_cost_result = run_backtest(oos_market, targets, no_cost_config)
        no_cost_portfolio = performance_metrics(no_cost_result.equity)
        annual_cost_drag = (
            no_cost_portfolio["annual_return"] - portfolio["annual_return"]
        )
        excess = portfolio["annual_return"] - benchmark_metrics["annual_return"]
        summaries[model_name] = {
            "ic": ic_summary,
            "portfolio": portfolio,
            "no_cost_portfolio": no_cost_portfolio,
            "annual_cost_drag": annual_cost_drag,
            "annual_excess_return_vs_benchmark": excess,
        }
        model_output = output / model_name
        model_output.mkdir(parents=True, exist_ok=True)
        ic.rename_axis("trade_date").reset_index().to_csv(
            model_output / "oos_ic.csv", index=False
        )
        targets.to_csv(model_output / "target_weights.csv", index=False)
        result.equity.to_csv(model_output / "equity.csv", index=False)
        no_cost_result.equity.to_csv(
            model_output / "equity_no_cost.csv", index=False
        )
        result.trades.to_csv(model_output / "trades.csv", index=False)
        report_rows.append(
            {
                "model": model_name,
                "mean_ic": ic_summary["mean_ic"],
                "icir": ic_summary["icir"],
                "ic_win_rate": ic_summary["win_rate"],
                "annual_return": portfolio["annual_return"],
                "no_cost_annual_return": no_cost_portfolio["annual_return"],
                "annual_cost_drag": annual_cost_drag,
                "sharpe": portfolio["sharpe"],
                "max_drawdown": portfolio["max_drawdown"],
                "turnover": portfolio["turnover"],
                "annual_excess_vs_benchmark": excess,
            }
        )

    sensitivity_rows: list[dict[str, Any]] = []
    for variant in settings.portfolio_variants:
        for model_name in model_names:
            targets = buffered_top_n_weights(
                predictions,
                signal_col=f"{model_name}_prediction",
                top_n=int(variant["top_n"]),
                exit_rank=int(variant["exit_rank"]),
                frequency=str(variant["frequency"]),
                max_weight=float(variant["max_weight"]),
                next_trading_date=next_trading_date,
            )
            result = run_backtest(oos_market, targets, backtest_config)
            no_cost_result = run_backtest(oos_market, targets, no_cost_config)
            portfolio = performance_metrics(result.equity)
            no_cost_portfolio = performance_metrics(no_cost_result.equity)
            sensitivity_rows.append(
                {
                    "variant": str(variant["name"]),
                    "model": model_name,
                    "frequency": str(variant["frequency"]),
                    "top_n": int(variant["top_n"]),
                    "exit_rank": int(variant["exit_rank"]),
                    "annual_return": portfolio["annual_return"],
                    "no_cost_annual_return": no_cost_portfolio["annual_return"],
                    "annual_cost_drag": (
                        no_cost_portfolio["annual_return"]
                        - portfolio["annual_return"]
                    ),
                    "sharpe": portfolio["sharpe"],
                    "max_drawdown": portfolio["max_drawdown"],
                    "turnover": turnover_from_trades(result.trades, result.equity),
                    "trades": len(result.trades),
                }
            )
    sensitivity = pd.DataFrame(sensitivity_rows)
    sensitivity.to_csv(
        output / "portfolio_sensitivity.csv", index=False, encoding="utf-8-sig"
    )

    summary = {
        "status": "OK",
        "data_source": scores.attrs.get("provider", "factor_scores"),
        "start_date": str(start.date()),
        "end_date": str(pd.Timestamp(predictions["trade_date"].max()).date()),
        "folds": len(folds),
        "features": list(settings.feature_names),
        "minimum_valid_features": settings.minimum_valid_features,
        "models": summaries,
        "benchmark": {
            **benchmark_metrics,
            "definition": (
                "point-in-time constituent equal-weight, daily rebalanced, "
                "before costs"
            ),
        },
        "environment": {
            "python": platform.python_version(),
            "numpy": _package_version("numpy"),
            "pandas": _package_version("pandas"),
            "lightgbm": _package_version("lightgbm"),
            "torch": _package_version("torch"),
        },
        "portfolio_sensitivity_variants": len(settings.portfolio_variants),
        "warning": (
            "Model comparison is out-of-sample but remains a research approximation; "
            "selection, costs, point-in-time fields and capacity still limit inference."
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    report_table = pd.DataFrame(report_rows)
    report_table.to_csv(output / "model_comparison.csv", index=False, encoding="utf-8-sig")
    report = f"""# 机器学习滚动样本外研究

> {summary["warning"]}

- 样本外区间：{summary["start_date"]} 至 {summary["end_date"]}
- 折数：{summary["folds"]}
- 特征数：{len(settings.feature_names)}；最少有效特征：{settings.minimum_valid_features}
- 训练/验证/测试：{settings.train_years}/{settings.validation_years}/{settings.test_years}年
- 隔离带：{settings.embargo_trading_days}个交易日

## 模型比较

{markdown_table(report_table)}

## 诊断基准

历史时点成分股每日等权、每日再平衡且不计成本。它不是官方中证500指数，也不是
可直接复制的投资组合。

{markdown_table(pd.DataFrame([benchmark_metrics]))}

## 特征稳定性

`mean_normalized_importance`按每折绝对重要性归一化；Ridge的`positive_share`接近0或1
表示系数方向较稳定，接近0.5表示方向不稳定。

{markdown_table(feature_stability)}

## 组合敏感性

以下变体在看结果前写入配置，只用于检查换手与成本机制，不改变主结果。

{markdown_table(sensitivity)}

## 解释边界

- 每折只使用测试年前的数据选择超参数，未来5日标签在边界处留出交易日隔离带。
- Ridge的缺失值填充、均值和标准差只从拟合样本学习。
- LightGBM和MLP是可选依赖；只有明确启用并实际运行后才能比较结果。
- 先比较等权与Ridge；复杂模型没有稳定样本外增益时，不应继续增加网络复杂度。
"""
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    return summary
