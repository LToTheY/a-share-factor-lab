"""Small, immutable local snapshots of the computation source for reproduction."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from uuid import uuid4

from quant_lab.research.service import code_version, digest, file_digest


def snapshot_path(root: Path, version: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", version):
        raise ValueError("无效计算代码指纹")
    return root / "data/state/code_snapshots" / f"{version}.zip"


def verify_snapshot(path: Path, expected_version: str) -> dict:
    """Validate every archived source byte; never extract or execute the archive."""
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("snapshot_manifest.json"))
            if manifest["code_version"] != expected_version:
                raise ValueError("源码快照指纹不匹配")
            # Source fingerprints historically used native path separators.
            # Accept the original Windows or POSIX spelling, even on another OS.
            variants = [manifest["files"], {name.replace("/", "\\"): value for name, value in manifest["files"].items()}]
            if expected_version not in {digest(files) for files in variants}:
                raise ValueError("源码快照文件摘要不能还原所记录的代码指纹")
            expected = set(manifest["files"]) | {"snapshot_manifest.json"}
            if len(archive.namelist()) != len(expected) or set(archive.namelist()) != expected:
                raise ValueError("源码快照文件清单不一致")
            for name, checksum in manifest["files"].items():
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts or not name.startswith("src/quant_lab/"):
                    raise ValueError("源码快照包含越界文件")
                if hashlib.sha256(archive.read(name)).hexdigest() != checksum:
                    raise ValueError("源码快照内容校验失败")
            return manifest
    except (OSError, zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"源码快照不可读：{path.name}") from exc


def snapshot_source(root: Path, expected_version: str | None = None) -> Path | None:
    root = root.resolve()
    version = code_version(root)
    if expected_version is not None and expected_version != version:
        raise ValueError("提交前计算代码已变化，请重新生成研究请求")
    paths = sorted((root / "src/quant_lab").rglob("*.py"))
    if not paths:
        return None  # Small isolated test roots contain no application source.
    if any(not path.resolve().is_relative_to(root / "src/quant_lab") for path in paths):
        raise ValueError("源码快照不接受指向计算目录外部的链接")
    destination = snapshot_path(root, version)
    if destination.exists():
        verify_snapshot(destination, version)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep only a short random name: appending a UUID to the full digest can
    # exceed Windows' legacy path limit in otherwise usable nested workspaces.
    temporary = destination.with_name("." + uuid4().hex[:16] + ".tmp")
    manifest = {"code_version": version, "scope": "quant_lab computation source",
                "files": {path.relative_to(root).as_posix(): file_digest(path) for path in paths}}
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                archive.write(path, path.relative_to(root).as_posix())
            archive.writestr("snapshot_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        verify_snapshot(temporary, version)
        if code_version(root) != version:
            raise ValueError("保存快照期间代码变化，未提交任务")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
