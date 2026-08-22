"""按用户显式命令下载 LongEval 2025 CORE Sci-Retrieval abstract 数据包。

本脚本只下载官方公开的 abstract ZIP，不下载 20 GiB 以上的 fulltext ZIP，
不解压、不解析数据，也不会被任何评测命令自动调用。
"""

from __future__ import annotations

import argparse
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkstemp
from typing import BinaryIO, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TRAIN_ABSTRACT_URL = "https://researchdata.tuwien.ac.at/records/r643n-yc044/files/longeval_sci_training_2025_abstract.zip?download=1"
TEST_ABSTRACT_URL = "https://researchdata.tuwien.ac.at/records/v8phe-g8911/files/longeval_sci_testing_2025_abstract.zip?download=1"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "longeval_2025" / "raw" / "archives"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class LongEvalAsset:
    """保存官方单个 abstract 数据包的稳定下载与校验信息。"""

    split: str
    filename: str
    url: str
    md5: str


ASSETS = {
    "train": LongEvalAsset(
        split="train",
        filename="longeval_sci_training_2025_abstract.zip",
        url=TRAIN_ABSTRACT_URL,
        md5="3918ccfc89653e878f54b06c311607c9",
    ),
    "test": LongEvalAsset(
        split="test",
        filename="longeval_sci_testing_2025_abstract.zip",
        url=TEST_ABSTRACT_URL,
        md5="091cf1931a0b4b17358a580012183c5e",
    ),
}


class LongEvalDownloadError(RuntimeError):
    """表示可安全展示给用户的下载或完整性校验失败。"""


@dataclass(frozen=True)
class DownloadedAsset:
    """保存经过官方 MD5 校验的本地 archive 信息。"""

    split: str
    path: Path
    size_bytes: int
    md5: str
    reused_existing_file: bool


UrlOpener = Callable[[Request, float], BinaryIO]


def build_parser() -> argparse.ArgumentParser:
    """构建只允许 abstract 数据包和显式联网许可的命令行入口。"""
    parser = argparse.ArgumentParser(description="下载 LongEval 2025 CORE Sci-Retrieval abstract 数据包")
    parser.add_argument("--split", action="append", choices=["train", "test", "all"], default=None, help="可重复指定 train、test；省略时只选择 train")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="本地 ZIP 输出目录，默认 data/evaluation/longeval_2025/raw/archives/")
    parser.add_argument("--allow-download", action="store_true", help="确认本次命令允许访问官方 TU Wien 数据仓库并下载大文件")
    parser.add_argument("--force", action="store_true", help="重新下载已有选择文件（包括校验失败文件）；下载成功后才原子替换")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="单次 HTTP 连接与读取超时秒数，默认 60")
    return parser


def resolve_assets(splits: Sequence[str] | None) -> list[LongEvalAsset]:
    """将用户选择展开为固定顺序、无重复的官方 abstract 资源。"""
    requested = list(splits) if splits else ["train"]
    if "all" in requested:
        requested = ["train", "test"]
    unknown = sorted(set(requested) - set(ASSETS))
    if unknown:
        raise ValueError(f"不支持的 LongEval split: {', '.join(unknown)}")
    return [ASSETS[name] for name in ("train", "test") if name in requested]


def download_longeval_dataset(
    *,
    splits: Sequence[str] | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    force: bool = False,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    opener: UrlOpener | None = None,
) -> list[DownloadedAsset]:
    """下载并以发布方 MD5 校验所选 abstract ZIP。

    函数仅由用户显式 CLI 或测试调用；它不解压 archive，不读取数据内容，也不调用
    ScholarFlow 生产服务、学术来源、LLM 或本地模型。
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于零")
    selected_assets = resolve_assets(splits)
    normalized_output_dir = output_dir.expanduser().resolve()
    normalized_output_dir.mkdir(parents=True, exist_ok=True)
    active_opener = opener or _open_url
    downloaded: list[DownloadedAsset] = []
    for asset in selected_assets:
        target_path = normalized_output_dir / asset.filename
        existing = _validate_existing_file(target_path, asset, force=force)
        if existing and not force:
            downloaded.append(DownloadedAsset(split=asset.split, path=target_path, size_bytes=target_path.stat().st_size, md5=asset.md5, reused_existing_file=True))
            continue
        _download_atomically(asset, target_path, timeout_seconds, active_opener)
        downloaded.append(DownloadedAsset(split=asset.split, path=target_path, size_bytes=target_path.stat().st_size, md5=asset.md5, reused_existing_file=False))
    return downloaded


def _validate_existing_file(path: Path, asset: LongEvalAsset, *, force: bool) -> bool:
    """校验已有目标；损坏 archive 必须由用户显式 `--force` 决定是否替换。"""
    if not path.exists():
        return False
    if not path.is_file():
        raise LongEvalDownloadError(f"下载目标不是普通文件：{path}")
    actual_md5 = _md5_file(path)
    if actual_md5 != asset.md5 and not force:
        raise LongEvalDownloadError(f"已有文件 MD5 不匹配：{path}；期望 {asset.md5}，实际 {actual_md5}。请人工检查后使用 --force 重新下载")
    return actual_md5 == asset.md5


def _download_atomically(asset: LongEvalAsset, target_path: Path, timeout_seconds: float, opener: UrlOpener) -> None:
    """下载到同目录临时文件，MD5 通过后再原子发布或替换目标。"""
    descriptor, temporary_name = mkstemp(prefix=f".{asset.filename}.", suffix=".part", dir=target_path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            request = Request(asset.url, headers={"User-Agent": "ScholarFlow-LongEval-Downloader/1.0"})
            try:
                response = opener(request, timeout_seconds)
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                raise LongEvalDownloadError(_describe_network_error(exc, asset)) from exc
            with response:
                _copy_stream(response, output)
        actual_md5 = _md5_file(temporary_path)
        if actual_md5 != asset.md5:
            raise LongEvalDownloadError(f"下载文件 MD5 不匹配：{asset.filename}；期望 {asset.md5}，实际 {actual_md5}。文件未发布，请检查网络后重试")
        os.replace(temporary_path, target_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _open_url(request: Request, timeout_seconds: float) -> BinaryIO:
    """执行唯一的官方仓库 HTTP 请求，便于测试以替身替换。"""
    return urlopen(request, timeout=timeout_seconds)


def _copy_stream(source: BinaryIO, destination: BinaryIO) -> None:
    """以固定块大小复制响应，避免将大 archive 一次载入内存。"""
    while True:
        chunk = source.read(DEFAULT_CHUNK_SIZE)
        if not chunk:
            return
        destination.write(chunk)


def _md5_file(path: Path) -> str:
    """返回文件的十六进制 MD5，仅用于核对发布方公开校验值。"""
    digest = hashlib.md5()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(DEFAULT_CHUNK_SIZE)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _describe_network_error(exc: Exception, asset: LongEvalAsset) -> str:
    """返回不含请求正文、环境变量或本地敏感信息的网络错误摘要。"""
    if isinstance(exc, HTTPError):
        return f"LongEval {asset.split} 下载失败：官方仓库返回 HTTP {exc.code}"
    if isinstance(exc, TimeoutError) or "timeout" in str(exc).casefold():
        return f"LongEval {asset.split} 下载超时；请检查网络后重试"
    return f"LongEval {asset.split} 下载网络失败；请检查官方仓库可访问性后重试"


def _format_size(size_bytes: int) -> str:
    """将文件大小格式化为稳定、可读的二进制单位。"""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size_bytes)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    raise AssertionError("文件大小单位循环不应到达此处")


def main(argv: Sequence[str] | None = None, *, opener: UrlOpener | None = None) -> int:
    """解析用户确认参数并执行下载；没有许可时在联网前失败。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.allow_download:
        parser.error("下载 LongEval 数据需要显式提供 --allow-download；本脚本不会自动联网")
    try:
        assets = download_longeval_dataset(splits=args.split, output_dir=args.output_dir, force=args.force, timeout_seconds=args.timeout_seconds, opener=opener)
    except (LongEvalDownloadError, ValueError) as exc:
        parser.error(str(exc))
    print(f"[OK] LongEval abstract 下载完成：{len(assets)} 个文件")
    for asset in assets:
        action = "复用已校验文件" if asset.reused_existing_file else "已下载并校验"
        print(f"[OK] {asset.split}: {action} {asset.path} ({_format_size(asset.size_bytes)}; md5={asset.md5})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
