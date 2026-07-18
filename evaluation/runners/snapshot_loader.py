"""提供候选快照 JSONL 的只读加载、哈希和语义完整性校验。"""

from pathlib import Path  # 只读打开用户显式提供的本地快照文件。

from evaluation.contracts.snapshot import CandidateSnapshot, compute_snapshot_hash  # 解析契约并核验内容哈希。
from evaluation.metrics.identifiers import papers_match  # 复用第一阶段论文身份去重规则。


def validate_snapshot_integrity(snapshot: CandidateSnapshot, *, require_hash: bool = True) -> str:
    """校验快照哈希和候选身份唯一性，并返回实际内容哈希。"""
    actual_hash = compute_snapshot_hash(snapshot)  # 按规范化 JSON 重新计算内容摘要。
    if require_hash and snapshot.snapshot_hash is None:  # 文件快照必须经过显式封存。
        raise ValueError(f"候选快照 {snapshot.snapshot_id} 缺少 snapshot_hash")  # 阻止无法验证复用身份的输入。
    if snapshot.snapshot_hash is not None and snapshot.snapshot_hash != actual_hash:  # 内容与声明摘要不一致说明已被修改。
        raise ValueError(f"候选快照 {snapshot.snapshot_id} 的 snapshot_hash 不匹配")  # 拒绝静默使用污染快照。
    for index, paper in enumerate(snapshot.papers):  # 逐条检查规范化去重承诺。
        duplicate = next((existing for existing in snapshot.papers[:index] if papers_match(paper, existing)), None)  # 只与此前候选比较。
        if duplicate is not None:  # 同一论文不能在排序前快照出现两次。
            raise ValueError(f"候选快照 {snapshot.snapshot_id} 含重复论文：{paper.paper_id} 与 {duplicate.paper_id}")  # 返回可定位身份。
    return actual_hash  # 返回供计划和运行结果冻结的实际摘要。


def load_candidate_snapshots(path: Path, *, require_hash: bool = True) -> list[CandidateSnapshot]:
    """以 UTF-8 只读加载快照 JSONL，拒绝重复标识、重复查询和被篡改内容。"""
    snapshots: list[CandidateSnapshot] = []  # 按文件顺序保存快照。
    with path.open("r", encoding="utf-8") as stream:  # 不创建、不修改输入文件。
        for line_number, raw_line in enumerate(stream, start=1):  # 使用一基行号定位契约错误。
            line = raw_line.strip()  # 忽略空白行和行尾换行。
            if not line:  # 空白行不构成候选快照。
                continue  # 继续读取下一行。
            try:  # 将 JSON、Pydantic 和完整性错误统一增加文件位置。
                snapshot = CandidateSnapshot.model_validate_json(line)  # 解析并执行结构校验。
                validate_snapshot_integrity(snapshot, require_hash=require_hash)  # 核验不可变内容和身份去重。
            except Exception as exc:  # 保留原始异常链供测试和人工定位。
                raise ValueError(f"{path} 第 {line_number} 行候选快照无效: {exc}") from exc  # 返回不包含候选正文的错误。
            snapshots.append(snapshot)  # 仅保存完成全部校验的快照。
    if not snapshots:  # 空文件无法形成任何离线比较任务。
        raise ValueError("候选快照文件不包含有效记录")  # 避免校验命令返回误导性成功。
    snapshot_ids = [snapshot.snapshot_id for snapshot in snapshots]  # 收集快照标识。
    if len(set(snapshot_ids)) != len(snapshot_ids):  # 重复快照标识会覆盖归档结果。
        raise ValueError("候选快照文件包含重复 snapshot_id")  # 拒绝歧义输入。
    query_ids = [snapshot.query_id for snapshot in snapshots]  # 收集查询标识。
    if len(set(query_ids)) != len(query_ids):  # 一个矩阵批次每条查询只允许一份在线快照。
        raise ValueError("候选快照文件包含重复 query_id")  # 防止不同在线候选被混入同一消融比较。
    return snapshots  # 返回只读校验后的快照列表。
