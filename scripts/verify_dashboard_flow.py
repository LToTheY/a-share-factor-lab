"""Exercise real Streamlit forms and workers using explicitly synthetic inputs.

Run manually after code changes settle; saves local demo experiments, never paper state.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

from quant_lab.dashboard.experiments import ExperimentStore
from quant_lab.research.jobs import JobStore
from quant_lab.research.service import file_digest


def widget(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def page(name, state=None):
    app = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=60)
    app._page_hash = calc_hash(name)  # AppTest.switch_page supports files, not callable pages.
    for key, value in (state or {}).items():
        app.session_state[key] = value
    app.run()
    assert not app.exception, app.exception
    return app


def wait_new_job(storage, before):
    jobs = [s for s in storage.list() if s["id"] not in before]
    assert len(jobs) == 1, jobs
    identifier = jobs[0]["id"]
    last = None
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        status = storage.get(identifier)
        if status["message"] != last:
            print(status["message"], flush=True)
            last = status["message"]
        if status["state"] not in {"queued", "running", "cancelling"}:
            assert status["state"] == "succeeded", (status, storage.log(identifier))
            return status
        time.sleep(1)
    raise TimeoutError("Synthetic UI acceptance task exceeded 30 minutes")


def main():
    paper = ROOT / "data/state/paper_portfolio.json"
    original = file_digest(paper) if paper.exists() else None
    storage = JobStore(ROOT)
    app = page("run-research")
    widget(app, "toggle", "合成数据演示（无需国泰安账号）").set_value(True).run()
    app.button(key="refresh_python_factors").click().run()
    widget(app, "multiselect", "参加研究的内置 / 自定义因子").set_value(["range_position_20"])
    before = {s["id"] for s in storage.list()}
    widget(app, "button", "启动因子研究").click().run()
    assert not app.exception and not app.error, (app.exception, app.error)
    research = wait_new_job(storage, before)
    app.run()
    app.button(key="open_" + research["id"]).click().run()
    report = app.session_state["report_dir"]
    ids = []
    reload = None
    for cash in (5000., 10000.):
        state = {"report_dir": report}
        if reload:
            state["reload_strategy"] = reload
        app = page("strategy-lab", state)
        widget(app, "text_input", "实验名称").set_value(f"合成网页验收 {int(cash)}元")
        widget(app, "number_input", "初始资金（元）").set_value(cash)
        before = {s["id"] for s in storage.list()}
        widget(app, "button", "运行策略回测").click().run()
        assert not app.exception and not app.error, (app.exception, app.error)
        job = wait_new_job(storage, before)
        result = json.loads((ROOT / job["result"] / "experiment.json").read_text(encoding="utf-8"))
        assert result["validation"]["status"] == "ready", result
        assert len(result["robustness"]) == 7, result
        ids.append(result["id"])
        history = page("experiment-history")
        widget(history, "button", "载入此实验参数到策略实验室").click().run()
        reload = history.session_state["reload_strategy"]
        widget(history, "text_area", "研究结论与失败复盘").set_value("合成流程验收通过；不构成真实因子有效性证据。")
        next(b for b in history.button if b.label == "保存结论").click().run()
        assert not history.exception
    history = page("experiment-history")
    comparison = widget(history, "selectbox", "对比实验（可选）")
    comparison.select(next(label for label in comparison.options if ids[0][:6] in label)).run()
    assert not history.exception
    experiments = ExperimentStore(ROOT / "data/state/strategy_experiments")
    first, second = [experiments.metadata(i) for i in ids]
    assert first["backtest"]["initial_cash"] == 5000 and second["backtest"]["initial_cash"] == 10000
    assert not experiments.table(ids[0], "trades").equals(experiments.table(ids[1], "trades"))
    assert experiments.notes(ids[0])["conclusion"]
    assert (file_digest(paper) if paper.exists() else None) == original
    result = {"status": "passed", "research_job": research["id"], "experiment_ids": ids,
              "source": "synthetic", "steps": ["custom_factor", "research", "backtest", "walk_forward", "robustness", "save", "reopen", "reload", "compare"], "paper_unchanged": True}
    (ROOT / "data/state/dashboard_flow_acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
