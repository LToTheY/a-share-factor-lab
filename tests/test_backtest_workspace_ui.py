from dataclasses import replace

import pytest
from streamlit.testing.v1 import AppTest
from test_experiment_history import sample_experiment

from dashboard.views import experiment_history
from quant_lab.dashboard.experiments import ExperimentStore


def test_history_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_history, "EXPERIMENT_ROOT", tmp_path)
    app = AppTest.from_string(
        "from dashboard.views.experiment_history import render\nrender()"
    ).run()
    assert not app.exception
    assert "还没有保存" in app.info[0].value


def test_history_compare_and_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_history, "EXPERIMENT_ROOT", tmp_path)
    result, spec, config, source = sample_experiment()
    storage = ExperimentStore(tmp_path)
    storage.save(result, spec, config, source, "基线")
    # A distinct saved configuration exercises the parameter comparison UI.
    storage.save(result, replace(spec, exit_rank=2), config, source, "对照")
    app = AppTest.from_string(
        "from dashboard.views.experiment_history import render\nrender()"
    ).run()
    assert not app.exception
    other = next(
        identifier for identifier in app.selectbox[1].options if identifier != "不对比"
    )
    app.selectbox[1].select(other).run()
    assert not app.exception
    assert any(title.value == "参数差异" for title in app.subheader)
    app.selectbox[-1].set_value(
        result.backtest.equity.trade_date.iloc[0].to_datetime64()
    ).run()
    assert not app.exception
    assert any("当日没有成交" in info.value for info in app.info)


@pytest.mark.parametrize("page", ["backtest", "strategy_lab"])
def test_existing_pages_render(page):
    app = AppTest.from_string(
        f"from dashboard.views.{page} import render\nrender()", default_timeout=30
    ).run()
    assert not app.exception


def test_result_shows_saved_conditions_not_current_form(monkeypatch):
    from dashboard.views import strategy_lab

    result, spec, config, source = sample_experiment()
    monkeypatch.setattr(
        strategy_lab, "_ui_test_result", (result, spec, config, source), raising=False
    )
    app = AppTest.from_string(
        "from dashboard.views.strategy_lab import _display_result, _ui_test_result\n"
        "_display_result(*_ui_test_result)"
    ).run()
    assert not app.exception
    assert any(
        metric.label == "初始资金" and metric.value == "10,000" for metric in app.metric
    )
