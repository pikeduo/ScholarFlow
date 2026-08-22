"""测试 LongEval abstract 下载脚本的选择、校验、原子发布和显式许可边界。"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from zipfile import ZipFile

import pytest

from scripts.download_longeval_dataset import ASSETS, EXTRACTION_MANIFEST_NAME, LongEvalDownloadError, download_longeval_dataset, extract_longeval_archives, main, resolve_assets


class _Response(BytesIO):
    """提供与 urlopen 响应兼容的无网络上下文管理器。"""

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        self.close()
        return False


def test_resolve_defaults_to_train_and_all_is_stably_ordered() -> None:
    """默认只选择较小范围的 train，all 按固定 train/test/qrels 顺序展开。"""
    assert [asset.split for asset in resolve_assets(None)] == ["train"]
    assert [asset.split for asset in resolve_assets(["all"])] == ["train", "test", "test-qrels"]
    with pytest.raises(ValueError, match="不支持的 LongEval split"):
        resolve_assets(["fulltext"])


def test_download_validates_md5_and_reuses_verified_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """首次下载必须校验，第二次在无 force 时仅复用已校验 archive。"""
    content = b"long-eval-train-fixture"
    asset = ASSETS["train"]
    monkeypatch.setitem(ASSETS, "train", asset.__class__(split=asset.split, filename=asset.filename, url=asset.url, md5=hashlib.md5(content).hexdigest()))
    calls: list[object] = []

    def opener(request: object, timeout: float) -> _Response:
        calls.append((request, timeout))
        return _Response(content)

    files = download_longeval_dataset(output_dir=tmp_path, opener=opener)
    assert files[0].path.read_bytes() == content
    assert files[0].reused_existing_file is False
    reused = download_longeval_dataset(output_dir=tmp_path, opener=opener)
    assert reused[0].reused_existing_file is True
    assert len(calls) == 1


def test_download_rejects_checksum_mismatch_without_publishing_file(tmp_path: Path) -> None:
    """错误正文不得覆盖目标文件或被误报为成功。"""
    with pytest.raises(LongEvalDownloadError, match="MD5 不匹配"):
        download_longeval_dataset(output_dir=tmp_path, opener=lambda request, timeout: _Response(b"corrupted"))
    assert not (tmp_path / ASSETS["train"].filename).exists()
    assert not list(tmp_path.glob("*.part"))


def test_existing_invalid_file_requires_force_and_network_errors_are_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """损坏已有文件仅能由 force 原子替换，HTTP 错误不回显服务响应。"""
    target = tmp_path / ASSETS["train"].filename
    target.write_bytes(b"old-invalid-data")
    with pytest.raises(LongEvalDownloadError, match="请人工检查后使用 --force"):
        download_longeval_dataset(output_dir=tmp_path, opener=lambda request, timeout: _Response(b"unused"))
    replacement = b"replacement-data"
    asset = ASSETS["train"]
    monkeypatch.setitem(ASSETS, "train", asset.__class__(split=asset.split, filename=asset.filename, url=asset.url, md5=hashlib.md5(replacement).hexdigest()))
    downloaded = download_longeval_dataset(output_dir=tmp_path, force=True, opener=lambda request, timeout: _Response(replacement))
    assert downloaded[0].reused_existing_file is False and target.read_bytes() == replacement
    with pytest.raises(LongEvalDownloadError, match="HTTP 503"):
        download_longeval_dataset(output_dir=tmp_path / "network", opener=lambda request, timeout: (_ for _ in ()).throw(HTTPError("https://example.invalid", 503, "failure", hdrs=None, fp=None)))


def test_cli_requires_explicit_download_permission(tmp_path: Path) -> None:
    """没有 --allow-download 时必须在任何网络调用前退出。"""
    with pytest.raises(SystemExit) as error:
        main(["--output-dir", str(tmp_path)], opener=lambda request, timeout: _Response(b"must-not-run"))
    assert error.value.code == 2


def test_extracts_verified_archive_atomically_and_reuses_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """已下载 archive 应在无网络条件下解压，并且 manifest 匹配时可安全复用。"""
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    archive_path = archive_dir / ASSETS["train"].filename
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("collection/queries.txt", "q1\tquery\n")
        archive.writestr("collection/documents/part-001.jsonl", "{\"id\": \"d1\"}\n")
    asset = ASSETS["train"]
    monkeypatch.setitem(ASSETS, "train", asset.__class__(split=asset.split, filename=asset.filename, url=asset.url, md5=hashlib.md5(archive_path.read_bytes()).hexdigest()))
    extracted = extract_longeval_archives(archive_dir=archive_dir, extract_dir=tmp_path / "extracted")
    assert (extracted[0].path / "collection" / "queries.txt").read_text(encoding="utf-8") == "q1\tquery\n"
    assert extracted[0].member_count == 2
    assert (extracted[0].path / EXTRACTION_MANIFEST_NAME).is_file()
    reused = extract_longeval_archives(archive_dir=archive_dir, extract_dir=tmp_path / "extracted")
    assert reused[0].reused_existing_directory is True


def test_rejects_zip_path_traversal_without_publishing_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """恶意 ZIP 条目不得逃逸到目标目录外，也不得留下已发布 split 目录。"""
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    archive_path = archive_dir / ASSETS["train"].filename
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "blocked")
    asset = ASSETS["train"]
    monkeypatch.setitem(ASSETS, "train", asset.__class__(split=asset.split, filename=asset.filename, url=asset.url, md5=hashlib.md5(archive_path.read_bytes()).hexdigest()))
    extract_dir = tmp_path / "extracted"
    with pytest.raises(LongEvalDownloadError, match="不安全路径"):
        extract_longeval_archives(archive_dir=archive_dir, extract_dir=extract_dir)
    assert not (extract_dir / "train").exists()
