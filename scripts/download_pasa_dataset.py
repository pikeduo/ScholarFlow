"""选择性下载用户已获授权的 PaSa 数据集文件，不保存任何 Hugging Face Token。"""

from __future__ import annotations  # 延迟求值类型标注，允许独立脚本在较旧解释器中安全导入。

import argparse  # 解析用户明确提供的下载范围和输出目录。
from dataclasses import dataclass  # 保存已下载文件的路径和大小。
from pathlib import Path  # 规范化仓库内默认输出路径和下载结果路径。
from typing import Callable, Sequence  # 声明可由离线测试替换且兼容现有解释器的下载函数类型。


PASA_REPOSITORY_ID = "CarlanLark/pasa-dataset"  # 固定用户指定的 Hugging Face 数据集仓库。
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "pasa"  # 默认将真实数据放入 Git 忽略的仓库数据目录。
SUBSET_PATTERNS = {  # 固定允许下载的最小文件集合，绝不默认拉取整个 gated 仓库。
    "auto": "AutoScholarQuery/dev.jsonl",  # 默认下载开发期可复现的 AutoScholarQuery 切分。
    "real": "RealScholarQuery/test.jsonl",  # 按需下载最终外部评测切分。
    "paper-database": "paper_database/id2paper.json",  # 按需下载本地论文 ID 到元数据映射。
}


class PasaDownloadError(RuntimeError):
    """表示可安全展示给用户的 PaSa 下载失败原因。"""


@dataclass(frozen=True)
class DownloadedFile:
    """保存一份选择性下载文件的绝对路径和字节大小。"""

    path: Path  # 保存用户可直接检查的本地文件路径。
    size_bytes: int  # 保存下载完成后实际读取到的文件大小。


def build_parser() -> argparse.ArgumentParser:
    """构建只包含选择性下载参数且不接受 Token 的命令行解析器。"""
    parser = argparse.ArgumentParser(description="选择性下载已获授权的 PaSa 数据集文件")  # 创建用户手动执行的维护脚本入口。
    parser.add_argument("--subset", action="append", choices=[*SUBSET_PATTERNS, "all"], default=None, help="可重复指定 auto、real、paper-database；省略时只下载 auto")  # 支持按需增加文件而不改变安全默认值。
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="本地输出目录，默认 data/evaluation/pasa/")  # 允许用户将大型数据写入其他受控位置。
    parser.add_argument("--revision", default=None, help="可选的 Hugging Face revision、tag 或 commit；省略时使用仓库默认版本")  # 支持用户冻结可复现实验版本。
    parser.add_argument("--force", action="store_true", help="强制重新下载所选文件，即使本地缓存或输出已存在")  # 显式控制 Hugging Face 缓存刷新行为。
    return parser  # 返回供 main 和离线测试共同使用的解析器。


def resolve_allow_patterns(subsets: Sequence[str] | None) -> list[str]:
    """将用户选择解析为去重且保持定义顺序的 Hugging Face allow_patterns。"""
    requested_subsets = list(subsets) if subsets else ["auto"]  # 未传参数时只请求默认开发集文件。
    if "all" in requested_subsets:  # all 是显式便利选项，不改变未指定时的最小下载默认值。
        requested_subsets = list(SUBSET_PATTERNS)  # 展开为所有受支持文件而非通配整个仓库。
    unknown_subsets = sorted(set(requested_subsets) - set(SUBSET_PATTERNS))  # 防御直接函数调用绕过 argparse choices。
    if unknown_subsets:  # 未知子集无法安全映射到受限文件路径。
        raise ValueError(f"不支持的 PaSa subset: {', '.join(unknown_subsets)}")  # 返回不包含认证信息的配置错误。
    selected_patterns: list[str] = []  # 保持固定字典定义顺序，方便日志和测试稳定比较。
    for subset_name, pattern in SUBSET_PATTERNS.items():  # 按预定义顺序决定 allow_patterns。
        if subset_name in requested_subsets:  # 只加入用户明确选择的文件。
            selected_patterns.append(pattern)  # 每个子集恰好对应一个受支持相对路径。
    return selected_patterns  # 返回传给 snapshot_download 的最小允许列表。


def download_pasa_dataset(
    *,
    subsets: Sequence[str] | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    revision: str | None = None,
    force: bool = False,
    snapshot_downloader: Callable[..., str] | None = None,
) -> list[DownloadedFile]:
    """选择性下载 PaSa 文件并返回经过存在性确认的路径和大小。

    参数：
        subsets：可选的 auto、real、paper-database 或 all；缺省时仅 auto。
        output_dir：用户显式确认的本地输出目录。
        revision：可选的仓库版本固定值。
        force：是否要求 SDK 忽略本地缓存重新下载。
        snapshot_downloader：测试注入的下载函数；正常执行时延迟导入 huggingface_hub。
    返回：
        list[DownloadedFile]：每份实际存在的选择文件及其字节大小。
    异常：
        PasaDownloadError：认证、网络、访问控制、远端文件或本地结果异常时抛出。
    """
    allow_patterns = resolve_allow_patterns(subsets)  # 在任何网络调用前固定最小文件范围。
    normalized_output_dir = output_dir.expanduser().resolve()  # 解析用户路径以便输出绝对可检查位置。
    downloader = snapshot_downloader or _load_snapshot_downloader()  # 正常运行才导入第三方 SDK，测试无需安装或联网。
    try:  # 将第三方异常转换为不泄露凭据的用户可操作提示。
        downloader(repo_id=PASA_REPOSITORY_ID, repo_type="dataset", revision=revision, local_dir=normalized_output_dir, allow_patterns=allow_patterns, force_download=force, token=True)  # token=True 仅使用本机 hf auth login 已保存凭据。
    except Exception as exc:  # Hugging Face 版本间异常类型可能演进，统一映射为稳定提示。
        raise PasaDownloadError(_describe_download_failure(exc, revision)) from exc  # 保留异常链供本地调试但不输出 Token。
    downloaded_files: list[DownloadedFile] = []  # 只报告经过本地存在性确认的选择文件。
    for relative_path in allow_patterns:  # 按允许文件顺序检查 snapshot_download 的 materialized 输出。
        local_path = normalized_output_dir / relative_path  # local_dir 保留仓库内相对目录结构。
        if not local_path.is_file():  # 成功返回却没有预期文件通常表示 revision 或远端布局错误。
            raise PasaDownloadError(f"下载完成但未找到预期文件：{local_path}；请确认 --revision 和 PaSa 仓库文件路径")  # 防止错误宣称下载成功。
        downloaded_files.append(DownloadedFile(path=local_path, size_bytes=local_path.stat().st_size))  # 冻结绝对路径和真实字节大小。
    return downloaded_files  # 供 CLI 输出与离线测试断言使用。


def _load_snapshot_downloader() -> Callable[..., str]:
    """延迟导入 huggingface_hub，缺失依赖时给出安装提示。"""
    try:  # 不在模块导入时要求用户已经安装可选下载依赖。
        from huggingface_hub import snapshot_download  # 仅在用户真正执行下载命令时加载官方 SDK。
    except ImportError as exc:  # 当前解释器未安装 requirements.txt 的新增依赖。
        raise PasaDownloadError("缺少 huggingface-hub 依赖；请使用当前项目解释器执行 pip install -r requirements.txt") from exc  # 返回明确、无网络副作用的修复命令。
    return snapshot_download  # 返回可接收 repo、allow_patterns、token 等参数的官方函数。


def _describe_download_failure(exc: Exception, revision: str | None) -> str:
    """将认证、gated、网络和文件缺失异常映射为明确且无敏感信息的提示。"""
    exception_name = type(exc).__name__  # 兼容不同 huggingface_hub 版本的异常模块位置。
    status_code = getattr(exc, "response", None) and getattr(exc.response, "status_code", None)  # 尝试读取 HTTP 错误状态而不暴露响应正文。
    if exception_name in {"GatedRepoError", "DisabledRepoError"} or status_code in {401, 403}:  # gated 数据集或本机认证无权限时统一提示授权步骤。
        return "无权访问 gated PaSa 数据集；请先在 Hugging Face 页面接受 CarlanLark/pasa-dataset 条款，再执行 hf auth login"  # 明确区分网页条款与本机凭据。
    if "Token" in exception_name or "Authentication" in exception_name:  # 本机没有 hf auth login 凭据时提供最短修复路径。
        return "未找到可用的 Hugging Face 登录凭据；请先执行 hf auth login，再重试下载"  # 不读取、不显示也不要求粘贴 Token。
    if exception_name in {"EntryNotFoundError", "LocalEntryNotFoundError", "RepositoryNotFoundError"} or status_code == 404:  # 仓库、版本或选择文件不存在时避免误报网络问题。
        revision_suffix = f"（revision={revision}）" if revision else ""  # 仅回显用户输入的非敏感版本标识。
        return f"未找到 PaSa 仓库、所选文件或指定版本{revision_suffix}；请检查 --revision 和访问权限"  # 返回可操作的路径与版本排查方向。
    if exception_name in {"IncompleteSnapshotError", "ConnectError", "ConnectionError", "ReadTimeout", "ConnectTimeout", "TimeoutError"} or "connection" in str(exc).lower() or "timeout" in str(exc).lower():  # 处理 SDK 网络和超时异常。
        return "PaSa 下载网络失败或超时；请检查网络、代理设置和 Hugging Face 可访问性后重试"  # 不包含第三方请求细节或环境变量。
    return f"PaSa 下载失败（{exception_name}）；请检查本机 Hugging Face 登录、网络、访问条款和 --revision"  # 对未知 SDK 错误提供安全通用提示。


def _format_size(size_bytes: int) -> str:
    """将非负字节大小显示为便于终端审阅的二进制单位。"""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]  # 固定常用二进制单位以避免引入额外依赖。
    value = float(size_bytes)  # 统一使用浮点数完成连续单位换算。
    for unit in units:  # 从最小单位依次尝试可读显示。
        if value < 1024.0 or unit == units[-1]:  # 小于下一单位或达到最大单位时停止换算。
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"  # 字节保持整数，其他单位保留一位小数。
        value /= 1024.0  # 继续换算到更大单位。
    raise AssertionError("文件大小单位循环不应到达此处")  # 保护未来修改单位列表时的不可达分支。


def main(argv: Sequence[str] | None = None, *, snapshot_downloader: Callable[..., str] | None = None) -> int:
    """解析命令参数、执行用户触发下载并输出文件路径和大小。"""
    parser = build_parser()  # 构造标准 argparse 错误行为。
    args = parser.parse_args(argv)  # 只解析命令行，不读取 Token 或调用网络。
    try:  # 统一将可预期下载失败转为命令行错误返回码。
        downloaded_files = download_pasa_dataset(subsets=args.subset, output_dir=args.output_dir, revision=args.revision, force=args.force, snapshot_downloader=snapshot_downloader)  # 仅在用户实际运行脚本时可能访问 Hugging Face。
    except PasaDownloadError as exc:  # 使用 argparse 的标准错误格式向用户显示安全摘要。
        parser.error(str(exc))  # 退出码为 2，且不会打印异常堆栈或认证信息。
    print(f"[OK] PaSa 选择性下载完成：{len(downloaded_files)} 个文件")  # 输出不含数据内容的完成摘要。
    for downloaded_file in downloaded_files:  # 逐个展示用户可检查的绝对文件路径和大小。
        print(f"[OK] {downloaded_file.path} ({_format_size(downloaded_file.size_bytes)})")  # 保持 Windows 终端友好的 ASCII 前缀。
    return 0  # 返回成功退出码供用户脚本或 CI 判断。


if __name__ == "__main__":  # 仅用户直接执行脚本时触发下载入口。
    raise SystemExit(main())  # 将整数退出码返回给操作系统。
