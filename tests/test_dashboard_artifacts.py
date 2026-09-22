import json

import pandas as pd
import pytest

from quant_lab.dashboard import ArtifactError, ArtifactStore


def _write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def test_json_and_csv_are_loaded_and_dates_are_parsed(tmp_path):
    (tmp_path / "summary.json").write_text(
        json.dumps({"factor_count": 1}), encoding="utf-8"
    )
    _write_csv(
        tmp_path / "factor_summary.csv",
        [
            {
                "factor": "momentum_20_5",
                "mean_ic": 0.02,
                "icir": 1.0,
                "win_rate": 0.55,
                "observations": 100,
            }
        ],
    )
    _write_csv(
        tmp_path / "ic_momentum_20_5.csv",
        [{"trade_date": "2025-01-02", "rank_ic": 0.1}],
    )
    store = ArtifactStore(tmp_path)

    assert store.json("summary.json")["factor_count"] == 1
    assert store.factor_names() == ["momentum_20_5"]
    assert pd.api.types.is_datetime64_any_dtype(
        store.factor_ic("momentum_20_5")["trade_date"]
    )


def test_schema_error_names_missing_columns(tmp_path):
    _write_csv(tmp_path / "factor_summary.csv", [{"factor": "x"}])

    with pytest.raises(ArtifactError, match="缺少字段"):
        ArtifactStore(tmp_path).csv("factor_summary.csv")


def test_path_cannot_escape_report_root(tmp_path):
    store = ArtifactStore(tmp_path / "reports")

    with pytest.raises(ArtifactError, match="禁止读取"):
        store.exists("../secret.txt")


def test_factor_snapshot_reads_only_latest_date_and_selected_factor(tmp_path):
    _write_csv(
        tmp_path / "factor_summary.csv",
        [
            {
                "factor": "factor_a",
                "mean_ic": 0.01,
                "icir": 0.5,
                "win_rate": 0.51,
                "observations": 2,
            }
        ],
    )
    pd.DataFrame(
        [
            {
                "trade_date": "2025-01-02",
                "symbol": "000001.SZ",
                "factor_a": 1.0,
                "factor_processed": 0.5,
                "close": 10.0,
                "in_universe": True,
            },
            {
                "trade_date": "2025-01-03",
                "symbol": "000002.SZ",
                "factor_a": 2.0,
                "factor_processed": 1.0,
                "close": 20.0,
                "in_universe": True,
            },
        ]
    ).to_parquet(tmp_path / "factor_scores.parquet", index=False)

    result = ArtifactStore(tmp_path).factor_snapshot("factor_a")

    assert result["symbol"].tolist() == ["000002.SZ"]
    assert result["factor_value"].tolist() == [2.0]


def test_unknown_factor_is_rejected_before_sql(tmp_path):
    _write_csv(
        tmp_path / "factor_summary.csv",
        [
            {
                "factor": "known",
                "mean_ic": 0.01,
                "icir": 0.5,
                "win_rate": 0.51,
                "observations": 2,
            }
        ],
    )

    with pytest.raises(ArtifactError, match="未知因子"):
        ArtifactStore(tmp_path).factor_snapshot('bad"factor')
