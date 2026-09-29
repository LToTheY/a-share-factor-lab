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
    comparison = next(item for item in app.selectbox if item.label == "对比实验（可选）")
    other = next(identifier for identifier in comparison.options if identifier != "不对比")
    comparison.select(other).run()
    assert not app.exception
    assert any(title.value == "参数差异" for title in app.subheader)
    next(item for item in app.selectbox if item.label == "回放交易日").set_value(
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


def test_result_shows_saved_conditions_not_current_form(tmp_path, monkeypatch):
    result, spec, config, source = sample_experiment()
    monkeypatch.setattr(experiment_history, "EXPERIMENT_ROOT", tmp_path)
    ExperimentStore(tmp_path).save(result, spec, config, source, "saved_conditions")
    app = AppTest.from_string(
        "from dashboard.views.experiment_history import render\nrender()"
    )
    app.session_state["reload_strategy"] = {"backtest": {"initial_cash": 5000}}
    app.run()
    assert not app.exception
    assert any(
        metric.label == "初始资金" and metric.value == "10,000" for metric in app.metric
    )


def test_module_template_equalizes_groups_and_reloaded_edits_take_effect():
    import pandas as pd

    from dashboard.views.strategy_lab import _signed_weights, factor_editor_frame

    frame = pd.DataFrame({"factor": ["momentum_20_5", "momentum_60_5", "reversal_5"], "configured_direction": 1.})
    editor = factor_editor_frame(frame, "模块等权（模块内等权）")
    assert editor.groupby("参考模块")["权重"].sum().to_dict() == {"动量": 1., "反转": 1.}
    loaded = factor_editor_frame(frame, "报告因子等权", {"reversal_5": -2.})
    assert _signed_weights(loaded) == {"reversal_5": -2.}
    loaded.loc[loaded["因子"].eq("reversal_5"), "权重"] = .5
    assert _signed_weights(loaded) == {"reversal_5": -.5}


def test_reloaded_table_edit_reaches_submitted_strategy(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pandas as pd

    from dashboard import context, research_runner
    from dashboard.views import strategy_lab
    from quant_lab.research import strategy_service
    from quant_lab.research.jobs import JobStore

    summary = pd.DataFrame({"factor": ["reversal_5"], "configured_direction": [1.]})
    artifact = SimpleNamespace(root=tmp_path / "reports/source", csv=lambda _: summary,
                               json=lambda name: {"universe": "CSI800"} if name == "dataset_provenance.json" else {"research_start_date": "2016-01-04"})
    monkeypatch.setattr(context, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(strategy_lab, "store", lambda: artifact)
    monkeypatch.setattr(strategy_lab, "StrategyDataStore", lambda *a: SimpleNamespace(date_bounds=lambda: (pd.Timestamp("2016-01-04"), pd.Timestamp("2025-01-03"))))
    monkeypatch.setattr(research_runner, "jobs_panel", lambda _: None)
    monkeypatch.setattr(research_runner, "version_notice", lambda _: None)
    monkeypatch.setattr(strategy_service, "prepare_strategy_request", lambda root, payload: payload)
    submitted = []
    monkeypatch.setattr(JobStore, "submit", lambda self, kind, payload: submitted.append(payload) or {"id": "a"*32})
    app = AppTest.from_string("from dashboard.views.strategy_lab import render\nrender()")
    app.session_state["reload_strategy"] = {"strategy": {"factor_weights": {"reversal_5": -2.}}}
    app.run()
    assert not app.exception
    editor_key = next(key for key in app.session_state._state.filtered_state if key.startswith("strategy_factor_editor_"))
    app.session_state[editor_key] = {"edited_rows": {0: {"权重": .5}}, "added_rows": [], "deleted_rows": []}
    next(button for button in app.button if button.label == "运行策略回测").click().run()
    assert not app.exception and not app.error
    assert submitted[0]["strategy"]["factor_weights"] == {"reversal_5": -.5}
