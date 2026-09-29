"""Real Windows sharing races and cancellation/publication ordering."""

import json
from pathlib import Path

import pytest

from quant_lab.research.jobs import JobStore, execute_job, read_json


def test_json_read_recovers_transient_permission_error(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    path.write_text('{"state":"succeeded"}', encoding="utf-8")
    original = Path.read_text
    calls = []

    def flaky(self, **kwargs):
        calls.append(self)
        if len(calls) < 3:
            raise PermissionError("sharing violation")
        return original(self, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky)
    monkeypatch.setattr("quant_lab.research.jobs.time.sleep", lambda _: None)
    assert read_json(path)["state"] == "succeeded"
    assert len(calls) == 3


def test_permanent_read_failure_is_bounded_and_corruption_is_not_hidden(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    path.write_text("broken json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_json(path)
    calls = []

    def denied(self, **kwargs):
        calls.append(self)
        raise PermissionError("permanent failure")

    monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.setattr("quant_lab.research.jobs.time.sleep", lambda _: None)
    with pytest.raises(PermissionError):
        read_json(path)
    assert len(calls) == 20


def test_cancel_after_current_result_publication_cannot_retract_success(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    job = store.submit("update", {}, launch=False)

    def published(*args, **kwargs):
        (store.path(job["id"]) / "cancel").touch()
        return {"status": "ready"}

    monkeypatch.setattr("quant_lab.research.current_check.run_current_check", published)
    execute_job(tmp_path, job["id"])
    assert store.get(job["id"])["state"] == "succeeded"
