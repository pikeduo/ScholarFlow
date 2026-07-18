"""提供默认离线、仅显式命令可导出在线候选的 ScholarFlow 评测能力。"""

from evaluation.runners.fixture import evaluate_records  # 暴露不访问网络的统一评测入口。

__all__ = ["evaluate_records"]  # 限制包级公共接口，避免调用方依赖内部实现。
