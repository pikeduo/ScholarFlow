"""提供与生产搜索隔离的 ScholarFlow 完全离线评测能力。"""

from evaluation.runners.fixture import evaluate_records  # 暴露不访问网络的统一评测入口。

__all__ = ["evaluate_records"]  # 限制包级公共接口，避免调用方依赖内部实现。
