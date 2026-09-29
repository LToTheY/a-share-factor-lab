"""Single-worker local jobs with durable status and cooperative cancellation."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from quant_lab.data.process_lock import data_lock
from quant_lab.research.service import ResearchRequest, digest, local_path, run_research
from quant_lab.research.snapshots import snapshot_source

ACTIVE = {"queued", "running", "cancelling"}


class JobCancelled(Exception):
    pass


def read_json(path: Path) -> dict:
    """Tolerate bounded Windows sharing races without hiding corrupt JSON."""
    for attempt in range(20):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    # Windows readers/antivirus can briefly hold the destination without delete sharing.
    # Keep atomic replacement, rather than truncating a status file being read by the UI.
    for attempt in range(20):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class JobStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / "data/state/jobs"

    def path(self, identifier: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("无效任务编号")
        return self.directory / identifier

    def get(self, identifier: str) -> dict:
        path = self.path(identifier) / "status.json"
        status = read_json(path)
        if status["state"] in ACTIVE and time.time() - status.get("heartbeat", time.time()) > 30 and not alive(status.get("pid", 0)):
            # Do not overwrite a live worker's completion; serialize with submit.
            with data_lock(self.directory / "registry.lock"):
                status = read_json(path)
                if status["state"] in ACTIVE and not alive(status.get("pid", 0)):
                    status.update(state="failed", message="后台进程已退出（可能关机）；已有成功结果保留")
                    atomic_json(path, status)
        return status

    def list(self) -> list[dict]:
        return sorted([self.get(p.parent.name) for p in self.directory.glob("*/status.json")],
                      key=lambda s: s["created_at"], reverse=True)

    def submit(self, kind: str, payload: dict, *, launch: bool = True) -> dict:
        if kind not in {"research", "strategy", "update", "manual_review"}:
            raise ValueError("未知任务类型")
        # Reconcile crashed workers before taking the registry lock.
        self.list()
        fingerprint = digest({"kind": kind, "payload": payload})
        with data_lock(self.directory / "registry.lock"):
            for path in self.directory.glob("*/status.json"):
                old = read_json(path)
                if old["state"] in ACTIVE:
                    if old["fingerprint"] == fingerprint:
                        return old
                    raise ValueError("已有重型任务运行中，请等待完成或取消后再提交")
            snapshot = snapshot_source(self.root, payload.get("code_version"))
            identifier = uuid4().hex
            folder = self.path(identifier)
            folder.mkdir(parents=True)
            atomic_json(folder / "request.json", {"kind": kind, "payload": payload})
            status = {"id": identifier, "kind": kind, "fingerprint": fingerprint, "state": "queued",
                      "created_at": datetime.now(timezone.utc).isoformat(), "message": "等待后台进程",
                      "heartbeat": time.time(), "pid": 0,
                      "source_snapshot": str(snapshot.relative_to(self.root)) if snapshot else None}
            atomic_json(folder / "status.json", status)
            if launch:
                try:
                    with (folder / "worker.log").open("w", encoding="utf-8") as log:
                        subprocess.Popen([sys.executable, "-X", "utf8", "-u", str(self.root / "scripts/run_job.py"), identifier],
                                         cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                except Exception:
                    status.update(state="failed", message="后台进程无法启动")
                    atomic_json(folder / "status.json", status)
                    raise
            return status

    def submit_research(self, config: dict) -> dict:
        request = ResearchRequest.create(self.root, config)
        return self.submit("research", asdict(request))

    def cancel(self, identifier: str) -> None:
        self.get(identifier)
        with data_lock(self.directory / "registry.lock"):
            status = read_json(self.path(identifier) / "status.json")
            if status["state"] in ACTIVE:
                (self.path(identifier) / "cancel").touch()

    def log(self, identifier: str) -> str:
        path = self.path(identifier) / "worker.log"
        if not path.exists():
            return "后台尚未输出日志"
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 16000))
            return handle.read().decode("utf-8", errors="replace")


def execute_job(root: Path, identifier: str) -> None:
    store = JobStore(root)
    folder = store.path(identifier)
    status = store.get(identifier)
    request = read_json(folder / "request.json")
    mutex = threading.Lock()
    stopped = threading.Event()

    def update(**values):
        with mutex:
            status.update(values, heartbeat=time.time())
            atomic_json(folder / "status.json", status)

    def progress(message):
        if (folder / "cancel").exists():
            raise JobCancelled("已取消；候选文件保留于本地，未覆盖任何成功结果")
        update(message=message)
        print(message, flush=True)

    def heartbeat():
        while not stopped.wait(3):
            update()

    ticker = threading.Thread(target=heartbeat, daemon=True)
    try:
        with data_lock(store.directory / "heavy.lock"):
            update(state="running", pid=os.getpid())
            ticker.start()
            progress("开始检查任务")
            kind, payload = request["kind"], request["payload"]
            if kind in {"update", "manual_review"}:
                from quant_lab.research.current_check import run_current_check

                if kind == "manual_review":
                    from quant_lab.research.manual_review import load_profile
                    profile = load_profile(root, payload["profile_id"])
                    result = run_current_check(root, local_path(root, profile["config_file"], area="data/state/manual_review"), progress_hook=progress, profile_id=profile["id"])
                else:
                    result = run_current_check(root, root / "configs/research.yaml", progress_hook=progress)
                if result.get("status") != "ready":
                    progress("检查数据更新结果")
                    raise ValueError(result.get("message") or result.get("error") or result.get("reason") or str(result))
                result_path = f"data/state/manual_review/{profile['id']}/status.json" if kind == "manual_review" else "data/state/current_check.json"
                # run_current_check has already atomically published success. A late
                # cancel cannot retract that visible result or relabel it cancelled.
                with data_lock(store.directory / "registry.lock"):
                    update(state="succeeded", message="已完成", result=result_path)
            else:
                with data_lock(root / "data/state/market_download.lock"):
                    candidate = root / "reports/generated/jobs" / (".pending-" + identifier)
                    candidate.mkdir(parents=True, exist_ok=True)
                    if kind == "research":
                        run_research(root, ResearchRequest(**payload), candidate, progress)
                    else:
                        from quant_lab.research.strategy_service import (
                            run_strategy_request,
                        )

                        run_strategy_request(root, payload, candidate, progress)
                    progress("保存完整结果")
                    destination = candidate.with_name(identifier)
                    # Serialize cancellation with publication for deterministic outcome.
                    with data_lock(store.directory / "registry.lock"):
                        progress("发布成功结果")
                        candidate.rename(destination)
                        if kind == "strategy":
                            experiment = read_json(destination / "experiment.json")
                            experiment_root = root / "data/state/strategy_experiments"
                            experiment_root.mkdir(parents=True, exist_ok=True)
                            (destination / "experiments" / experiment["id"]).rename(experiment_root / experiment["id"])
                        result_path = str(destination.relative_to(root))
                        update(state="succeeded", message="已完成", result=result_path)
            update(state="succeeded", message="已完成", result=result_path)
    except JobCancelled as exc:
        update(state="cancelled", message=str(exc))
    except Exception as exc:  # noqa: BLE001 -- worker boundary must persist every failure
        traceback.print_exc()
        update(state="failed", message=f"{type(exc).__name__}: {exc}")
    finally:
        stopped.set()
        if ticker.is_alive():
            ticker.join(timeout=5)


def read_result(root: Path, status: dict) -> Path:
    if status["state"] != "succeeded":
        raise ValueError("任务尚无完整结果")
    return local_path(root, status["result"])
