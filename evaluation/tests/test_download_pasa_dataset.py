"""测试 PaSa 选择性下载脚本的参数、文件校验和安全错误映射。"""

from pathlib import Path  # 使用 pytest 临时目录构造完全离线的下载输出。

import pytest  # 验证异常边界和命令入口行为。

from scripts.download_pasa_dataset import PASA_REPOSITORY_ID, PasaDownloadError, download_pasa_dataset, main, resolve_allow_patterns  # 导入用户脚本并注入 mock 下载函数。


def _write_selected_files(**kwargs: object) -> str:
    """模拟 snapshot_download，只在指定 local_dir 写入 allow_patterns 文件。"""
    output_dir = Path(str(kwargs["local_dir"]))  # 读取脚本传入的目标输出目录。
    for relative_path in kwargs["allow_patterns"]:  # 只创建 allow_patterns 指定文件以验证选择性下载。
        target_path = output_dir / relative_path  # 保持 Hugging Face local_dir 的相对目录结构。
        target_path.parent.mkdir(parents=True, exist_ok=True)  # 创建合成文件的父目录。
        target_path.write_bytes(b"fixture-data")  # 写入固定大小的无敏感合成内容。
    return str(output_dir)  # 模拟官方函数返回本地快照根目录。


def test_default_download_only_requests_autoscholarquery_dev(tmp_path: Path) -> None:
    """未指定 subset 时只应请求默认 AutoScholarQuery/dev.jsonl。"""
    calls: list[dict[str, object]] = []  # 捕获 mock 函数收到的官方 SDK 参数。

    def downloader(**kwargs: object) -> str:
        """记录调用参数后创建与 allow_patterns 对应的本地文件。"""
        calls.append(dict(kwargs))  # 复制参数以避免调用方后续修改影响断言。
        return _write_selected_files(**kwargs)  # 只模拟本地文件 materialization，不访问网络。

    files = download_pasa_dataset(output_dir=tmp_path / "pasa", snapshot_downloader=downloader)  # 运行默认零网络下载闭环。
    assert len(calls) == 1  # 每次脚本运行只调用一次 snapshot_download。
    assert calls[0]["repo_id"] == PASA_REPOSITORY_ID  # 固定使用用户指定数据集仓库。
    assert calls[0]["repo_type"] == "dataset"  # 明确声明 Hugging Face dataset 仓库类型。
    assert calls[0]["allow_patterns"] == ["AutoScholarQuery/dev.jsonl"]  # 默认不请求 train、test 或论文数据库。
    assert calls[0]["token"] is True  # 强制 SDK 从本机 hf auth login 凭据读取认证信息。
    assert calls[0]["force_download"] is False  # 默认不强制刷新已有缓存。
    assert files[0].path.name == "dev.jsonl" and files[0].size_bytes == len(b"fixture-data")  # 返回实际存在文件的稳定路径和字节大小。


def test_optional_subsets_expand_to_exact_allow_patterns(tmp_path: Path) -> None:
    """real 与 paper-database 应只追加各自一个预定义文件。"""
    calls: list[dict[str, object]] = []  # 保存 mock 下载参数供严格比较。

    def downloader(**kwargs: object) -> str:
        """记录参数并创建请求的合成文件。"""
        calls.append(dict(kwargs))  # 保存本次调用快照。
        return _write_selected_files(**kwargs)  # 模拟选择性下载的本地结果。

    files = download_pasa_dataset(subsets=["paper-database", "real", "auto"], output_dir=tmp_path / "pasa", revision="revision-test", force=True, snapshot_downloader=downloader)  # 请求三个受支持文件。
    assert calls[0]["allow_patterns"] == ["AutoScholarQuery/dev.jsonl", "RealScholarQuery/test.jsonl", "paper_database/id2paper.json"]  # 输出保持预定义安全顺序而非用户输入偶然顺序。
    assert calls[0]["revision"] == "revision-test" and calls[0]["force_download"] is True  # 透传用户显式的可复现版本和强制刷新选择。
    assert len(files) == 3  # 只报告三个实际请求并创建的文件。


def test_download_rejects_missing_materialized_file(tmp_path: Path) -> None:
    """下载函数返回后缺少选择文件时不得误报为成功。"""
    with pytest.raises(PasaDownloadError, match="未找到预期文件"):  # 验证本地结果校验边界。
        download_pasa_dataset(output_dir=tmp_path / "pasa", snapshot_downloader=lambda **_: str(tmp_path / "pasa"))  # 模拟 SDK 未 materialize 所需文件。


def test_gated_or_unauthorized_error_is_actionable(tmp_path: Path) -> None:
    """gated 数据集错误应提示接受条款和执行 hf auth login，而不显示 Token。"""
    class GatedRepoError(Exception):
        """用同名测试异常模拟不同 SDK 版本中的 gated 错误类型。"""

    def downloader(**_: object) -> str:
        """模拟 Hugging Face 拒绝未接受条款或无权限访问。"""
        raise GatedRepoError("access denied")  # 不携带任何真实认证信息。

    with pytest.raises(PasaDownloadError, match="接受 CarlanLark/pasa-dataset 条款，再执行 hf auth login"):  # 验证可操作的安全提示。
        download_pasa_dataset(output_dir=tmp_path / "pasa", snapshot_downloader=downloader)


def test_resolver_and_cli_do_not_allow_unbounded_repository_patterns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """解析器只允许预定义路径，CLI 成功摘要不包含数据内容。"""
    assert resolve_allow_patterns(["all"]) == ["AutoScholarQuery/dev.jsonl", "RealScholarQuery/test.jsonl", "paper_database/id2paper.json"]  # all 仍展开为有限文件列表。
    with pytest.raises(ValueError, match="不支持的 PaSa subset"):  # 直接调用时也拒绝未知范围。
        resolve_allow_patterns(["*"])
    exit_code = main(["--output-dir", str(tmp_path / "pasa")], snapshot_downloader=_write_selected_files)  # 用 mock 执行默认 CLI 分支。
    output = capsys.readouterr().out  # 读取命令行安全摘要。
    assert exit_code == 0 and "PaSa 选择性下载完成：1 个文件" in output  # CLI 应只报告计数、路径和大小。
    assert "fixture-data" not in output  # 终端不得输出任何数据文件内容。
