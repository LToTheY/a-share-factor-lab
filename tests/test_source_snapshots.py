"""An archived version must reproduce its source fingerprint, without secrets."""

import json
import zipfile

import pytest

from quant_lab.research.service import code_version, file_digest
from quant_lab.research.snapshots import snapshot_source, verify_snapshot


def test_snapshots_reuse_exact_code_and_exclude_accounts(tmp_path):
    source = tmp_path / "src/quant_lab/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("private placeholder", encoding="utf-8")
    first_version = code_version(tmp_path)
    path = snapshot_source(tmp_path, first_version)
    original = file_digest(path)
    assert snapshot_source(tmp_path, first_version) == path
    assert file_digest(path) == original
    assert set(verify_snapshot(path, first_version)["files"]) == {"src/quant_lab/example.py"}
    source.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="已变化"):
        snapshot_source(tmp_path, first_version)
    next_path = snapshot_source(tmp_path)
    assert next_path != path and file_digest(path) == original


def test_modified_archive_is_rejected_even_if_manifest_checksum_is_changed(tmp_path):
    source = tmp_path / "src/quant_lab/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    version = code_version(tmp_path)
    path = snapshot_source(tmp_path)
    manifest = verify_snapshot(path, version)
    name = "src/quant_lab/example.py"
    import hashlib
    payload = b"value = 99\n"
    manifest["files"][name] = hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, payload)
        archive.writestr("snapshot_manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError, match="代码指纹"):
        verify_snapshot(path, version)
