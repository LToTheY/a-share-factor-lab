from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

from dashboard.custom_factors import research_config
from quant_lab.factors import custom_loader as loader
from quant_lab.factors.library import FACTOR_REGISTRY, compute_factor

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src/quant_lab/factors/custom/_template.py").read_text(
    encoding="utf-8"
)


@pytest.fixture
def custom_dir(tmp_path, monkeypatch):
    registry = dict(FACTOR_REGISTRY)
    monkeypatch.setattr(loader, "CUSTOM_DIR", tmp_path)
    monkeypatch.setattr(loader, "_loaded", False)
    monkeypatch.setattr(loader, "_names", set())
    monkeypatch.setattr(loader, "_metadata", {})
    monkeypatch.setattr(loader, "_results", [])
    yield tmp_path
    FACTOR_REGISTRY.clear()
    FACTOR_REGISTRY.update(registry)


def save(directory, text=TEMPLATE, name="my_momentum"):
    path = directory / f"{name}.py"
    path.write_text(text, encoding="utf-8")
    return path


def test_reload_change_error_delete_and_builtin_protection(custom_dir):
    path = save(custom_dir)
    save(custom_dir, name="_ignored")
    save(custom_dir, name="reversal_5")
    rows = loader.refresh_custom_factors()
    assert len(rows) == 2
    assert rows[0]["status"] == "已加载"
    assert rows[1]["status"] == "失败"
    first = loader.custom_factor_metadata()["my_momentum"]["sha256"]
    path.write_text(
        TEMPLATE.replace('TITLE = "我的20日动量"', 'TITLE = "改后的名称"'),
        encoding="utf-8",
    )
    loader.refresh_custom_factors()
    assert loader.custom_factor_metadata()["my_momentum"]["sha256"] != first
    assert loader.custom_factor_metadata()["my_momentum"]["title"] == "改后的名称"
    path.write_text("this is not python!", encoding="utf-8")
    assert loader.refresh_custom_factors()[0]["status"] == "失败"
    assert "my_momentum" not in FACTOR_REGISTRY
    assert "reversal_5" in FACTOR_REGISTRY
    save(custom_dir)
    loader.refresh_custom_factors()
    path.unlink()
    loader.refresh_custom_factors()
    assert "my_momentum" not in FACTOR_REGISTRY


@pytest.mark.parametrize(
    "expression",
    [
        "price.to_numpy()",
        "price.reset_index(drop=True)",
        "pd.Series(float('inf'), index=frame.index)",
        "price.groupby(frame['symbol']).shift(-1)",
    ],
)
def test_bad_outputs_and_future_values_are_rejected(custom_dir, expression):
    save(custom_dir, TEMPLATE.replace("price / previous - 1.0", expression))
    assert loader.refresh_custom_factors()[0]["status"] == "失败"
    assert "my_momentum" not in FACTOR_REGISTRY


def test_auto_load_and_actual_pipeline_output(custom_dir):
    save(custom_dir)
    dates = pd.bdate_range("2024-01-01", periods=30)
    frame = pd.concat(
        [
            pd.DataFrame(
                {
                    "trade_date": dates,
                    "symbol": name,
                    "adj_close": np.arange(1, 31) * scale,
                }
            )
            for name, scale in [("A", 1), ("B", 1000)]
        ],
        ignore_index=True,
    ).sample(frac=1, random_state=8)
    result = compute_factor(frame, "my_momentum")
    latest = result[result.trade_date.eq(dates[-1])]
    assert np.allclose(latest.factor, 2.0)
    assert (
        result.groupby("symbol").factor.apply(lambda x: x.iloc[:20].isna().all()).all()
    )
    with pytest.raises(ValueError, match="缺少输入字段"):
        compute_factor(frame.drop(columns="adj_close"), "my_momentum")


def test_independent_config_does_not_change_daily_settings(custom_dir):
    import yaml

    save(custom_dir)
    loader.refresh_custom_factors()
    base = yaml.safe_load((ROOT / "configs/research.yaml").read_text(encoding="utf-8"))
    original = yaml.safe_dump(base)
    config = research_config(base, ["my_momentum"], loader.custom_factor_metadata())
    assert yaml.safe_dump(base) == original
    assert config["factors"]["definitions"] == [{"name": "my_momentum", "direction": 1}]
    assert config["factors"]["minimum_valid_factors"] == 1
    assert config["project"]["output_dir"] != base["project"]["output_dir"]
    assert (
        config["paper_account"]["next_orders_file"]
        != base["paper_account"]["next_orders_file"]
    )


def test_frontend_update_and_tutorial(custom_dir):
    app = AppTest.from_file(str(ROOT / "dashboard/app.py"))
    app._page_hash = calc_hash("factor-library")
    app.run()
    assert not app.exception
    assert any("模板怎么填" in item.label for item in app.expander)
    assert any("def compute" in item.value for item in app.code)
    path = save(custom_dir)
    app.button(key="refresh_python_factors").click().run()
    assert not app.exception
    assert any(item.value == "我的20日动量" for item in app.subheader)
    path.write_text(TEMPLATE.replace("我的20日动量", "新版本标题"), encoding="utf-8")
    app.button(key="refresh_python_factors").click().run()
    assert any(item.value == "新版本标题" for item in app.subheader)
    path.write_text("bad code !", encoding="utf-8")
    app.button(key="refresh_python_factors").click().run()
    assert not app.exception
    assert app.error
    assert not any(item.value == "新版本标题" for item in app.subheader)


def test_frontend_open_report(custom_dir, monkeypatch):
    from dashboard import custom_factors

    report = custom_dir / "research_report"
    report.mkdir()
    monkeypatch.setattr(custom_factors, "DEFAULT_REPORT_DIR", report)
    app = AppTest.from_file(str(ROOT / "dashboard/app.py"))
    app._page_hash = calc_hash("factor-library")
    app.run()
    app.button(key="open_custom_factor_report").click().run()
    assert not app.exception
    assert app.session_state["report_dir"] == str(report)
